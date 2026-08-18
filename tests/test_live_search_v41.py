from pathlib import Path

from backend.app.evidence import build_recommendation
from backend.app.taste_store import TasteStore


def test_high_volume_google_direct_search_can_recommend_without_menu_keyword():
    row = {
        "provider": "google",
        "provider_id": "g-direct-1",
        "province": "경기도",
        "city": "테스트시",
        "name": "테스트 로컬 식당",
        "category": "한식",
        "cuisine": "한식",
        "address": "경기도 테스트시 테스트로 1",
        "road_address": "경기도 테스트시 테스트로 1",
        "rating": 4.2,
        "user_rating_count": 783,
        "query_text": "테스트시 테스트 로컬 식당",
        "query_category": "전체",
        "discovery_mode": "direct_search",
    }
    item = build_recommendation([row], "경기도", "테스트시")
    assert item is not None
    assert item["user_rating_count"] == 783
    assert item["rating"] == 4.2


def test_direct_search_upsert_does_not_delete_existing_region_rows(tmp_path: Path):
    store = TasteStore(tmp_path / "taste.sqlite3")
    base = {
        "provider_id": "base-1",
        "name": "기존 맛집",
        "cuisine": "한식",
        "rating": 4.4,
        "user_rating_count": 300,
        "taste_score": 70,
        "query_hits": 1,
        "raw_json": {"evidence": {"sources": ["google"]}},
    }
    direct = {
        "provider_id": "direct-1",
        "name": "직접 검색 맛집",
        "cuisine": "한식",
        "rating": 4.2,
        "user_rating_count": 783,
        "taste_score": 72,
        "query_hits": 1,
        "raw_json": {"evidence": {"sources": ["google"]}},
    }
    store.replace_region("경기도", "테스트시", [base])
    store.upsert_region("경기도", "테스트시", [direct])
    rows = store.get_region("경기도", "테스트시", 10)
    assert {r["name"] for r in rows} == {"기존 맛집", "직접 검색 맛집"}
