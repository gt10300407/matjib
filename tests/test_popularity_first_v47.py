from pathlib import Path

from backend.app.collectors.google_places import SEARCH_SPECS, _is_mass_market_fast_food as google_fast_food
from backend.app.collectors.kakao import POPULARITY_QUERY_SPECS, _is_mass_market_fast_food as kakao_fast_food
from backend.app.services import TOP_RECOMMENDATIONS, _popularity_sort_key


ROOT = Path(__file__).resolve().parents[1]


def test_v472_uses_small_local_taste_query_set():
    assert len(POPULARITY_QUERY_SPECS) == 7
    assert len(SEARCH_SPECS) == 5
    assert any("로컬 맛집" in tpl for _, tpl, _ in POPULARITY_QUERY_SPECS)
    assert any("로컬 맛집" in tpl for _, tpl, _ in SEARCH_SPECS)
    kakao_text = (ROOT / "backend" / "app" / "collectors" / "kakao.py").read_text(encoding="utf-8")
    assert "_grid_rects" not in kakao_text
    assert "짜장면 맛집" not in kakao_text
    assert "삼겹살 맛집" not in kakao_text


def test_normal_refresh_does_not_crawl_citywide_public_master():
    service_text = (ROOT / "backend" / "app" / "services.py").read_text(encoding="utf-8")
    refresh_body = service_text.split("async def refresh(self, province, city, bbox=None):", 1)[1]
    assert "PublicDataCollector" not in refresh_body
    assert "licensed_inventory" not in refresh_body
    assert '"public_api_calls": 0' in refresh_body


def test_region_result_is_top_ten():
    assert TOP_RECOMMENDATIONS == 10
    service_text = (ROOT / "backend" / "app" / "services.py").read_text(encoding="utf-8")
    assert "recommendations = recommendations[:TOP_RECOMMENDATIONS]" in service_text


def test_local_taste_evidence_outranks_raw_google_review_volume():
    local_gem = {
        "rating": 4.2,
        "user_rating_count": 350,
        "query_hits": 4,
        "source_count": 2,
        "taste_score": 58,
        "evidence": {
            "keyword_source_hits": {"google": 2, "kakao": 4},
            "keyword_queries": ["안산시 로컬 맛집", "안산시 현지인 맛집", "안산시 오래된 맛집"],
        },
    }
    rating_heavy = {
        "rating": 4.5,
        "user_rating_count": 5000,
        "query_hits": 1,
        "source_count": 1,
        "taste_score": 72,
        "evidence": {
            "keyword_source_hits": {"google": 1},
            "keyword_queries": ["안산시 맛집"],
        },
    }
    assert _popularity_sort_key(local_gem) > _popularity_sort_key(rating_heavy)


def test_google_paid_calls_are_bounded_and_not_retried():
    google_text = (ROOT / "backend" / "app" / "collectors" / "google_places.py").read_text(encoding="utf-8")
    assert "Paid API: deliberately no automatic retry" in google_text
    assert "for attempt in range" not in google_text
    assert '"nearby_popularity_calls": 0' in google_text
    assert "SEARCH_SPECS" in google_text
    assert "asyncio.Semaphore(5)" in google_text


def test_explicit_taste_queries_feed_existing_evidence_contract():
    google_text = (ROOT / "backend" / "app" / "collectors" / "google_places.py").read_text(encoding="utf-8")
    kakao_text = (ROOT / "backend" / "app" / "collectors" / "kakao.py").read_text(encoding="utf-8")
    assert '_normalize_place(p, province, city, query, "keyword")' in google_text
    assert 'self._normalize(d, province, city, query, category, "keyword")' in kakao_text
    assert "keyword_popularity" not in google_text
    assert "keyword_popularity" not in kakao_text


def test_fast_food_filter_is_taxonomy_based_not_brand_hardcoding():
    assert google_fast_food({"primaryType": "fast_food_restaurant", "types": ["restaurant"]}) is True
    assert google_fast_food({"primaryType": "korean_restaurant", "types": ["restaurant"]}) is False
    assert kakao_fast_food({"category_name": "음식점 > 패스트푸드 > 햄버거"}) is True
    assert kakao_fast_food({"category_name": "음식점 > 한식 > 국밥"}) is False
    google_text = (ROOT / "backend" / "app" / "collectors" / "google_places.py").read_text(encoding="utf-8")
    kakao_text = (ROOT / "backend" / "app" / "collectors" / "kakao.py").read_text(encoding="utf-8")
    assert "롯데리아" not in google_text
    assert "롯데리아" not in kakao_text
