from backend.app.evidence import build_recommendation


def _row(provider: str, **extra):
    row = {
        "provider": provider,
        "provider_id": f"{provider}-1",
        "province": "경기도",
        "city": "안산시",
        "name": "테스트식당",
        "address": "경기도 안산시 단원구 테스트로 1",
        "road_address": "경기도 안산시 단원구 테스트로 1",
        "category": "한식",
        "cuisine": "한식",
        "query_category": "공식정보",
        "query_text": "안산시 모범음식점",
        "discovery_mode": "official",
        "verified_public": provider == "excellent",
        "raw_json": {},
    }
    row.update(extra)
    return row


def test_official_only_is_not_a_taste_recommendation():
    assert build_recommendation([_row("excellent")], "경기도", "안산시") is None


def test_official_plus_generic_kakao_is_still_not_enough():
    kakao = _row(
        "kakao",
        provider_id="k-1",
        query_category="전체",
        query_text="안산시 맛집",
        discovery_mode="keyword",
        verified_public=False,
        raw_json={"category_name": "음식점 > 한식"},
    )
    assert build_recommendation([_row("excellent"), kakao], "경기도", "안산시") is None


def test_official_can_boost_an_already_google_qualified_place():
    google = _row(
        "google",
        provider_id="g-1",
        query_category="전체",
        query_text="안산시 맛집",
        discovery_mode="nearby_popularity",
        verified_public=False,
        rating=4.5,
        user_rating_count=120,
        primary_type="korean_restaurant",
        raw_json={"types": ["korean_restaurant", "restaurant"], "primaryType": "korean_restaurant"},
    )
    item = build_recommendation([google, _row("excellent")], "경기도", "안산시")
    assert item is not None
    assert item["evidence"]["official_excellent"] is True
    assert item["evidence"]["score_components"]["official_data"] == 10.0
