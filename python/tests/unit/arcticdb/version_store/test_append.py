import random
import pandas as pd
import numpy as np
from datetime import datetime
import pytest
import sys
from numpy.testing import assert_array_equal

from pandas import MultiIndex
from arcticdb.version_store import NativeVersionStore
from arcticdb_ext.exceptions import InternalException, NormalizationException, SortingException
from arcticdb_ext import set_config_int
from arcticdb.util.test import random_integers, assert_frame_equal
from arcticdb.config import set_log_level


def test_append_simple(lmdb_version_store):
    symbol = "test_append_simple"
    df1 = pd.DataFrame({"x": np.arange(1, 10, dtype=np.int64)})
    lmdb_version_store.write(symbol, df1)
    vit = lmdb_version_store.read(symbol)
    assert_frame_equal(vit.data, df1)

    df2 = pd.DataFrame({"x": np.arange(11, 20, dtype=np.int64)})
    lmdb_version_store.append(symbol, df2)
    vit = lmdb_version_store.read(symbol)
    expected = pd.concat([df1, df2], ignore_index=True)
    assert_frame_equal(vit.data, expected)


def test_append_indexed(s3_version_store):
    symbol = "test_append_simple"
    idx1 = np.arange(0, 10)
    d1 = {"x": np.arange(10, 20, dtype=np.int64)}
    df1 = pd.DataFrame(data=d1, index=idx1)
    s3_version_store.write(symbol, df1)
    vit = s3_version_store.read(symbol)
    assert_frame_equal(vit.data, df1)

    idx2 = np.arange(10, 20)
    d2 = {"x": np.arange(20, 30, dtype=np.int64)}
    df2 = pd.DataFrame(data=d2, index=idx2)
    s3_version_store.append(symbol, df2)
    vit = s3_version_store.read(symbol)
    expected = pd.concat([df1, df2])
    assert_frame_equal(vit.data, expected)


def test_append_string_of_different_sizes(lmdb_version_store):
    symbol = "test_append_simple"
    df1 = pd.DataFrame(data={"x": ["cat", "dog"]}, index=np.arange(0, 2))
    lmdb_version_store.write(symbol, df1)
    vit = lmdb_version_store.read(symbol)
    assert_frame_equal(vit.data, df1)

    df2 = pd.DataFrame(data={"x": ["catandsomethingelse", "dogandsomethingevenlonger"]}, index=np.arange(2, 4))
    lmdb_version_store.append(symbol, df2)
    vit = lmdb_version_store.read(symbol)
    expected = pd.concat([df1, df2])
    assert_frame_equal(vit.data, expected)


def test_append_snapshot_delete(lmdb_version_store):
    symbol = "test_append_snapshot_delete"
    if sys.platform == "win32":
        # Keep it smaller on Windows due to restricted LMDB size
        row_count = 1000
    else:
        row_count = 1000000
    idx1 = np.arange(0, row_count)
    d1 = {"x": np.arange(row_count, 2 * row_count, dtype=np.int64)}
    df1 = pd.DataFrame(data=d1, index=idx1)
    lmdb_version_store.write(symbol, df1)
    vit = lmdb_version_store.read(symbol)
    assert_frame_equal(vit.data, df1)

    lmdb_version_store.snapshot("my_snap")

    idx2 = np.arange(row_count, 2 * row_count)
    d2 = {"x": np.arange(2 * row_count, 3 * row_count, dtype=np.int64)}
    df2 = pd.DataFrame(data=d2, index=idx2)
    lmdb_version_store.append(symbol, df2)
    vit = lmdb_version_store.read(symbol)
    expected = pd.concat([df1, df2])
    assert_frame_equal(vit.data, expected)

    lmdb_version_store.delete(symbol)
    assert lmdb_version_store.list_versions() == []

    assert_frame_equal(lmdb_version_store.read(symbol, as_of="my_snap").data, df1)


def _random_integers(size, dtype):
    # We do not generate integers outside the int64 range
    platform_int_info = np.iinfo("int_")
    iinfo = np.iinfo(dtype)
    return np.random.randint(
        max(iinfo.min, platform_int_info.min), min(iinfo.max, platform_int_info.max), size=size
    ).astype(dtype)


def test_append_out_of_order_throws(lmdb_version_store):
    lib: NativeVersionStore = lmdb_version_store
    lib.write("a", pd.DataFrame({"c": [1, 2, 3]}, index=pd.date_range(0, periods=3)))
    with pytest.raises(Exception, match="1970-01-03"):
        lib.append("a", pd.DataFrame({"c": [4]}, index=pd.date_range(1, periods=1)))


def test_append_out_of_order_and_sort(lmdb_version_store_ignore_order):
    symbol = "out_of_order"
    lmdb_version_store_ignore_order.version_store.remove_incomplete(symbol)

    num_rows = 1111
    dtidx = pd.date_range("1970-01-01", periods=num_rows)
    test = pd.DataFrame(
        {"uint8": _random_integers(num_rows, np.uint8), "uint32": _random_integers(num_rows, np.uint32)}, index=dtidx
    )
    chunk_size = 100
    list_df = [test[i : i + chunk_size] for i in range(0, test.shape[0], chunk_size)]
    random.shuffle(list_df)

    first = True
    for df in list_df:
        if first:
            lmdb_version_store_ignore_order.write(symbol, df)
            first = False
        else:
            lmdb_version_store_ignore_order.append(symbol, df)

    lmdb_version_store_ignore_order.version_store.sort_index(symbol, True)
    vit = lmdb_version_store_ignore_order.read(symbol)
    assert_frame_equal(vit.data, test)


def test_upsert_with_delete(lmdb_version_store_big_map):
    lib = lmdb_version_store_big_map
    symbol = "upsert_with_delete"
    lib.version_store.remove_incomplete(symbol)
    lib.version_store._set_validate_version_map()

    num_rows = 1111
    dtidx = pd.date_range("1970-01-01", periods=num_rows)
    test = pd.DataFrame(
        {"uint8": _random_integers(num_rows, np.uint8), "uint32": _random_integers(num_rows, np.uint32)}, index=dtidx
    )
    chunk_size = 100
    list_df = [test[i : i + chunk_size] for i in range(0, test.shape[0], chunk_size)]

    for idx, df in enumerate(list_df):
        if idx % 3 == 0:
            lib.delete(symbol)

        lib.append(symbol, df, write_if_missing=True)

    first = list_df[len(list_df) - 3]
    second = list_df[len(list_df) - 2]
    third = list_df[len(list_df) - 1]

    expected = pd.concat([first, second, third])
    vit = lib.read(symbol)
    assert_frame_equal(vit.data, expected)


def test_append_numpy_array(lmdb_version_store):
    np1 = random_integers(10, np.uint32)
    lmdb_version_store.write("test_append_numpy_array", np1)
    np2 = random_integers(10, np.uint32)
    lmdb_version_store.append("test_append_numpy_array", np2)
    vit = lmdb_version_store.read("test_append_numpy_array")
    expected = np.concatenate((np1, np2))
    assert_array_equal(vit.data, expected)


def test_append_pickled_symbol(lmdb_version_store):
    symbol = "test_append_pickled_symbol"
    lmdb_version_store.write(symbol, np.arange(100).tolist())
    assert lmdb_version_store.is_symbol_pickled(symbol)
    with pytest.raises(InternalException):
        _ = lmdb_version_store.append(symbol, np.arange(100).tolist())


def test_append_not_sorted_exception(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_initial_rows)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=dtidx)
    assert df.index.is_monotonic_increasing == True

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "ASCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx = np.roll(pd.date_range(initial_timestamp, periods=num_rows), 3)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == False

    with pytest.raises(SortingException):
        lmdb_version_store.append(symbol, df2, validate_index=True)


def test_append_existing_not_sorted_exception(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx = np.roll(pd.date_range(initial_timestamp, periods=num_initial_rows), 3)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=dtidx)
    assert df.index.is_monotonic_increasing == False

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == True

    with pytest.raises(SortingException):
        lmdb_version_store.append(symbol, df2, validate_index=True)


def test_append_not_sorted_non_validate_index(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_initial_rows)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=dtidx)
    assert df.index.is_monotonic_increasing == True

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "ASCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx = np.roll(pd.date_range(initial_timestamp, periods=num_rows), 3)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == False
    lmdb_version_store.append(symbol, df2)


def test_append_not_sorted_multi_index_exception(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx1 = pd.date_range(initial_timestamp, periods=num_initial_rows)
    dtidx2 = np.roll(np.arange(0, num_initial_rows), 3)
    df = pd.DataFrame(
        {"c": np.arange(0, num_initial_rows, dtype=np.int64)},
        index=pd.MultiIndex.from_arrays([dtidx1, dtidx2], names=["datetime", "level"]),
    )
    assert isinstance(df.index, MultiIndex) == True
    assert df.index.is_monotonic_increasing == True

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "ASCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx1 = np.roll(pd.date_range(initial_timestamp, periods=num_rows), 3)
    dtidx2 = np.arange(0, num_rows)
    df2 = pd.DataFrame(
        {"c": np.arange(0, num_rows, dtype=np.int64)},
        index=pd.MultiIndex.from_arrays([dtidx1, dtidx2], names=["datetime", "level"]),
    )
    assert df2.index.is_monotonic_increasing == False
    assert isinstance(df.index, MultiIndex) == True

    with pytest.raises(SortingException):
        lmdb_version_store.append(symbol, df2, validate_index=True)


def test_append_not_sorted_range_index_non_exception(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    dtidx = pd.RangeIndex(0, num_initial_rows, 1)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=dtidx)

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "ASCENDING"

    num_rows = 20
    dtidx = pd.RangeIndex(num_initial_rows, num_initial_rows + num_rows, 1)
    dtidx = np.roll(dtidx, 3)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == False
    with pytest.raises(NormalizationException):
        lmdb_version_store.append(symbol, df2)


def test_append_mix_ascending_not_sorted(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_initial_rows)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=dtidx)
    assert df.index.is_monotonic_increasing == True

    lmdb_version_store.write(symbol, df, validate_index=True)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "ASCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == True
    lmdb_version_store.append(symbol, df2, validate_index=True)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "ASCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2021-01-01")
    dtidx = np.roll(pd.date_range(initial_timestamp, periods=num_rows), 3)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == False
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2022-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == True
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"


def test_append_mix_descending_not_sorted(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_initial_rows)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=reversed(dtidx))
    assert df.index.is_monotonic_decreasing == True

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "DESCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=reversed(dtidx))
    assert df2.index.is_monotonic_decreasing == True
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "DESCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2021-01-01")
    dtidx = np.roll(pd.date_range(initial_timestamp, periods=num_rows), 3)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_decreasing == False
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2022-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=reversed(dtidx))
    assert df2.index.is_monotonic_decreasing == True
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"


def test_append_mix_ascending_descending(lmdb_version_store):
    symbol = "bad_append"

    num_initial_rows = 20
    initial_timestamp = pd.Timestamp("2019-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_initial_rows)
    df = pd.DataFrame({"c": np.arange(0, num_initial_rows, dtype=np.int64)}, index=reversed(dtidx))
    assert df.index.is_monotonic_decreasing == True

    lmdb_version_store.write(symbol, df)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "DESCENDING"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2020-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=dtidx)
    assert df2.index.is_monotonic_increasing == True
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"

    num_rows = 20
    initial_timestamp = pd.Timestamp("2022-01-01")
    dtidx = pd.date_range(initial_timestamp, periods=num_rows)
    df2 = pd.DataFrame({"c": np.arange(0, num_rows, dtype=np.int64)}, index=reversed(dtidx))
    assert df2.index.is_monotonic_decreasing == True
    lmdb_version_store.append(symbol, df2)
    info = lmdb_version_store.get_info(symbol)
    assert info["sorted"] == "UNSORTED"


@pytest.mark.xfail(reason="Needs to be fixed with issue #496")
def test_append_with_cont_mem_problem(sym, lmdb_version_store_tiny_segment_dynamic):
    set_config_int("SymbolDataCompact.SegmentCount", 1)
    df0 = pd.DataFrame({"0": ["01234567890123456"]}, index=[pd.Timestamp(0)])
    df1 = pd.DataFrame({"0": ["012345678901234567"]}, index=[pd.Timestamp(1)])
    df2 = pd.DataFrame({"0": ["0123456789012345678"]}, index=[pd.Timestamp(2)])
    df3 = pd.DataFrame({"0": ["01234567890123456789"]}, index=[pd.Timestamp(3)])
    df = pd.concat([df0, df1, df2, df3])

    for _ in range(100):
        lib = lmdb_version_store_tiny_segment_dynamic
        lib.write(sym, df0).version
        lib.append(sym, df1).version
        lib.append(sym, df2).version
        lib.append(sym, df3).version
        lib.version_store.defragment_symbol_data(sym, None)
        res = lib.read(sym).data
        assert_frame_equal(df, res)


def test_append_docs_example(lmdb_version_store):
    # This test is really just the append example from the docs.
    # Other examples are included so that outputs can be easily re-generated.
    lib = lmdb_version_store

    # Write example
    cols = ["COL_%d" % i for i in range(50)]
    df = pd.DataFrame(np.random.randint(0, 50, size=(25, 50)), columns=cols)
    df.index = pd.date_range(datetime(2000, 1, 1, 5), periods=25, freq="H")
    print(df.head(2))
    lib.write("test_frame", df)

    # Read it back
    from_storage_df = lib.read("test_frame").data
    print(from_storage_df.head(2))

    # Slicing and filtering examples
    print(lib.read("test_frame", date_range=(df.index[5], df.index[8])).data)
    _range = (df.index[5], df.index[8])
    _cols = ["COL_30", "COL_31"]
    print(lib.read("test_frame", date_range=_range, columns=_cols).data)
    from arcticdb import QueryBuilder

    q = QueryBuilder()
    q = q[(q["COL_30"] > 30) & (q["COL_31"] < 50)]
    print(lib.read("test_frame", date_range=_range, colymns=_cols, query_builder=q).data)

    # Update example
    random_data = np.random.randint(0, 50, size=(25, 50))
    df2 = pd.DataFrame(random_data, columns=["COL_%d" % i for i in range(50)])
    df2.index = pd.date_range(datetime(2000, 1, 1, 5), periods=25, freq="H")
    df2 = df2.iloc[[0, 2]]
    print(df2)
    lib.update("test_frame", df2)
    print(lib.head("test_frame", 2))

    # Append example
    random_data = np.random.randint(0, 50, size=(5, 50))
    df_append = pd.DataFrame(random_data, columns=["COL_%d" % i for i in range(50)])
    print(df_append)
    df_append.index = pd.date_range(datetime(2000, 1, 2, 7), periods=5, freq="H")

    lib.append("test_frame", df_append)
    print(lib.tail("test_frame", 7).data)
    expected = pd.concat([df2, df.drop(df.index[:3]), df_append])
    assert_frame_equal(lib.read("test_frame").data, expected)

    print(lib.tail("test_frame", 7, as_of=0).data)


def test_read_incomplete_no_warning(s3_store_factory, sym, get_stderr):
    lib = s3_store_factory(dynamic_strings=True, incomplete=True)
    symbol = sym

    write_df = pd.DataFrame({"a": [1, 2, 3]}, index=pd.DatetimeIndex([1, 2, 3]))
    lib.append(symbol, write_df, incomplete=True)
    # Need to compact so that the APPEND_REF points to a non-existent APPEND_DATA (intentionally)
    lib.compact_incomplete(symbol, True, False, False, True)
    set_log_level("DEBUG")

    try:
        read_df = lib.read(symbol, date_range=(pd.to_datetime(0), pd.to_datetime(10))).data
        assert_frame_equal(read_df, write_df.tz_localize("UTC"))

        err = get_stderr()
        assert err.count("W arcticdb.storage | Failed to find segment for key") == 0
        assert err.count("D arcticdb.storage | Failed to find segment for key") == 1
    finally:
        set_log_level()
