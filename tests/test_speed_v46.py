from pathlib import Path

import backend.app.evidence as evidence


ROOT = Path(__file__).resolve().parents[1]


def _row(provider, provider_id, name, address, *, rating=0.0, reviews=0, query="안산시 맛집", mode="keyword"):
    return {
        "provider": provider,
        "provider_id": provider_id,
        "province": "경기도",
        "city": "안산시",
        "name": name,
        "category": "한식",
        "cuisine": "한식",
        "business_type": "restaurant",
        "address": address,
        "road_address": address,
        "phone": None,
        "x": None,
        "y": None,
        "place_url": None,
        "status": "테스트",
        "verified_public": False,
        "rating": rating,
        "user_rating_count": reviews,
        "query_text": query,
        "query_category": "전체",
        "query_hits": 1,
        "discovery_mode": mode,
    }


def test_indexed_entity_resolution_keeps_same_place_merge():
    rows = [
        _row("kakao", "k1", "산골미꾸라지매운탕", "경기도 안산시 단원구 소바위길 12"),
        _row("google", "g1", "산골 미꾸라지 매운탕", "경기도 안산시 단원구 소바위길 12", rating=4.2, reviews=783, mode="nearby_popularity"),
    ]
    out = evidence.merge_and_rank(rows, "경기도", "안산시")
    assert len(out) == 1
    assert out[0]["user_rating_count"] == 783
    assert set(out[0]["sources"]) == {"google", "kakao"}


def test_indexed_entity_resolution_avoids_global_quadratic_scan(monkeypatch):
    rows = [
        _row("kakao", f"k{i}", f"서로다른식당{i}", f"경기도 안산시 테스트로 {1000+i}")
        for i in range(500)
    ]
    calls = 0
    original = evidence.entity_match_score

    def counted(a, b):
        nonlocal calls
        calls += 1
        return original(a, b)

    monkeypatch.setattr(evidence, "entity_match_score", counted)
    evidence.merge_and_rank(rows, "경기도", "안산시")
    # Old all-cluster scan is ~124,750 comparisons for 500 unrelated rows.
    # Blocking should stay well below that without changing the final scorer.
    assert calls < 10_000


def test_collectors_keep_same_coverage_but_use_bounded_parallelism():
    kakao = (ROOT / "backend/app/collectors/kakao.py").read_text(encoding="utf-8")
    google = (ROOT / "backend/app/collectors/google_places.py").read_text(encoding="utf-8")
    public = (ROOT / "backend/app/collectors/publicdata.py").read_text(encoding="utf-8")
    assert "asyncio.Semaphore(16)" in kakao
    assert "asyncio.Semaphore(9)" in google
    assert "parallel_after_first_page" in public
    assert "asyncio.Semaphore(4)" in public


def test_mobile_web_uses_true_nonblocking_refresh_controller():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend/app-refresh-async.js").read_text(encoding="utf-8")
    assert "/static/app-refresh-async.js" in html
    assert "/static/app-fast-refresh.js" not in html
    assert "cloneNode(true)" in js
    assert "/api/v1/region/refresh-async" in js
    assert "/api/v1/region/refresh-status" in js
    assert "/api/v1/region/refresh`" not in js
