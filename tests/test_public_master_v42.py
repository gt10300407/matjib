from pathlib import Path

from backend.app.collectors.publicdata import _is_active, normalize_general_row, total_count
from backend.app.services import (
    _attach_public_inventory,
    _belongs_to_requested_region,
    _query_matches_name,
)
from backend.app.taste_store import TasteStore


def _raw(name="산골미꾸라지매운탕", address="경기도 안산시 단원구 소바위길 12", status="영업/정상"):
    return {
        "MNG_NO": "A-1",
        "BPLC_NM": name,
        "ROAD_NM_ADDR": address,
        "SITE_WHL_ADDR": "경기도 안산시 단원구 선부동",
        "SALS_STTS_NM": status,
        "UPTAE_NM": "한식",
        "TELNO": "031-000-0000",
        "X": "181000.0",
        "Y": "430000.0",
    }


def test_public_master_normalization_does_not_misuse_epsg5174_as_wgs84():
    row = normalize_general_row(_raw(), "경기도", "안산시")
    assert row["provider"] == "general"
    assert row["name"] == "산골미꾸라지매운탕"
    assert row["road_address"] == "경기도 안산시 단원구 소바위길 12"
    assert row["x"] is None and row["y"] is None
    assert row["discovery_mode"] == "official_inventory"


def test_closed_rows_are_excluded_but_unknown_status_is_not_guessed_closed():
    assert _is_active(_raw(status="영업/정상")) is True
    assert _is_active(_raw(status="폐업")) is False
    row = _raw(status="")
    row.pop("SALS_STTS_NM")
    assert _is_active(row) is True


def test_total_count_finds_nested_public_api_metadata():
    payload = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 1234, "items": []}}}
    assert total_count(payload) == 1234


def test_live_search_requires_requested_city_or_bbox():
    wrong_city = {
        "name": "산골추어탕",
        "road_address": "경기도 시흥시 군자동 22-2",
        "x": "126.80",
        "y": "37.35",
    }
    assert _belongs_to_requested_region(wrong_city, "안산시", [126.55, 37.20, 126.95, 37.40]) is False

    correct = {
        "name": "산골미꾸라지매운탕",
        "road_address": "경기도 안산시 단원구 소바위길 12",
    }
    assert _belongs_to_requested_region(correct, "안산시", None) is True


def test_query_name_match_rejects_similar_but_different_menu_word():
    assert _query_matches_name("매운탕", "산골미꾸라지매운탕") is True
    assert _query_matches_name("매운탕", "산골추어탕") is False


def test_public_inventory_attaches_only_to_matching_discovered_place():
    inv = normalize_general_row(_raw(), "경기도", "안산시")
    discovered = [{
        "provider": "google",
        "provider_id": "g1",
        "province": "경기도",
        "city": "안산시",
        "name": "산골미꾸라지매운탕",
        "road_address": "대한민국 경기도 안산시 단원구 소바위길 12",
        "address": "대한민국 경기도 안산시 단원구 소바위길 12",
        "phone": None,
        "x": "126.80",
        "y": "37.32",
    }]
    rows = _attach_public_inventory(discovered, [inv])
    assert len(rows) == 2
    assert {r["provider"] for r in rows} == {"google", "general"}


def test_public_inventory_persists_and_searches_by_name(tmp_path: Path):
    store = TasteStore(tmp_path / "taste.sqlite3")
    row = normalize_general_row(_raw(), "경기도", "안산시")
    assert store.replace_public_inventory("경기도", "안산시", [row]) == 1
    assert store.public_inventory_is_fresh("경기도", "안산시", 20) is True
    found = store.search_public_inventory("경기도", "안산시", "미꾸라지", 10)
    assert len(found) == 1
    assert found[0]["name"] == "산골미꾸라지매운탕"
