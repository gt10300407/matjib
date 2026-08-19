from pathlib import Path

from backend.app.taste_store import TasteStore


ROOT = Path(__file__).resolve().parents[1]


def test_consumer_cards_hide_developer_scoring_and_show_useful_facts():
    js = (ROOT / "frontend" / "app-user-info.js").read_text(encoding="utf-8")
    assert "userRank" in js
    assert "Google 평가" in js
    assert "로컬 맛집 검색" in js
    assert "현지인 맛집 검색" in js
    assert "Google · Kakao 확인" in js
    assert "최근 확인" in js
    assert "장소 보기" in js
    assert "taste_score" not in js
    assert "score_components" not in js
    assert "추천 근거 보기" not in js


def test_consumer_layer_is_loaded_after_refresh_controller():
    js = (ROOT / "frontend" / "app-refresh-async.js").read_text(encoding="utf-8")
    assert "/static/app-user-info.js" in js
    assert "data-user-info-layer" in js


def test_verified_store_persists_phone_for_user_cards(tmp_path):
    store = TasteStore(tmp_path / "taste.sqlite3")
    row = {
        "provider_id": "agg-1",
        "name": "테스트맛집",
        "cuisine": "기타",
        "primary_type": "restaurant",
        "address": "경기도 안산시 테스트로 1",
        "phone": "031-123-4567",
        "x": "126.8",
        "y": "37.3",
        "place_url": "https://example.com/place",
        "rating": 4.4,
        "user_rating_count": 321,
        "taste_score": 70,
        "query_hits": 3,
        "raw_json": {"evidence": {"sources": ["google", "kakao"]}},
    }
    store.replace_region("경기도", "안산시", [row])
    saved = store.get_region("경기도", "안산시", 10)
    assert saved[0]["phone"] == "031-123-4567"
