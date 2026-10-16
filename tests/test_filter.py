import pytest

from src import filter
from src.fetch import parse_area, parse_floor, parse_yen


def room(**kw):
    base = {"total": 150000, "area": 50.0, "floor": 3, "layout": "2DK"}
    base.update(kw)
    return base


def test_parse_values():
    assert parse_yen("164,300円") == 164300
    assert parse_yen("125,000～150,000円") == 150000
    assert parse_yen("") is None
    assert parse_yen(None) is None
    assert parse_area("54&#13217;") == 54.0
    assert parse_area("45.5㎡") == 45.5
    assert parse_floor("26階") == 26
    assert parse_floor(None) is None


def test_layout_rank_order():
    order = ["1R", "1K", "1DK", "1LDK", "2K", "2DK", "2LDK", "2SLDK", "3DK", "3LDK"]
    ranks = [filter.layout_rank(x) for x in order]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)
    assert filter.layout_rank("１ＬＤＫ".replace("１", "1")) == filter.layout_rank("1LDK")
    assert filter.layout_rank("メゾネット") is None


@pytest.mark.parametrize(
    "total, expected",
    [(200000, True), (200001, False), (None, False)],
)
def test_max_total_rent(total, expected):
    assert filter.matches(room(total=total), {"max_total_rent": 200000}) is expected


def test_optional_filters_disabled_by_null():
    filters = {"max_total_rent": 200000, "min_area": None, "min_layout": None, "min_floor": None}
    assert filter.matches(room(area=None, floor=None, layout=""), filters)


def test_min_area():
    f = {"max_total_rent": 200000, "min_area": 50}
    assert filter.matches(room(area=50.0), f)
    assert not filter.matches(room(area=49.9), f)
    assert not filter.matches(room(area=None), f)


def test_min_floor():
    f = {"max_total_rent": 200000, "min_floor": 3}
    assert filter.matches(room(floor=3), f)
    assert not filter.matches(room(floor=2), f)


def test_min_layout():
    f = {"max_total_rent": 200000, "min_layout": "1DK"}
    assert filter.matches(room(layout="1DK"), f)
    assert filter.matches(room(layout="2K"), f)
    assert not filter.matches(room(layout="1K"), f)
    assert not filter.matches(room(layout="1R"), f)


def test_bad_min_layout_raises():
    with pytest.raises(ValueError):
        filter.matches(room(), {"min_layout": "広い"})
