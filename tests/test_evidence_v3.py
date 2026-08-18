from backend.app.evidence import build_recommendation, merge_and_rank, same_place


def _row(provider, name="백년교동짬뽕 안산장상동점", query="안산시 짬뽕 맛집", **extra):
    row = {
        "provider": provider,
        "provider_id": f"{provider}:{query}:{name}",
        "province": "경기도",
        "city": "안산시",
        "name": name,
        "category": "중식",
        "query_category": "중식",
        "query_text": query,
        "query_hits": 1,
        "discovery_mode": "keyword",
        "address": "경기도 안산시 상록구 양달말길 5",
        "road_address": "경기도 안산시 상록구 양달말길 5",
        "x": "126.87",
        "y": "37.32",
    }
    row.update(extra)
    return row


def test_local_famous_place_can_pass_without_google():
    cluster = [
        _row("kakao", query="안산시 중국집"),
        _row("kakao", query="안산시 짜장면 맛집"),
        _row("kakao", query="안산시 짬뽕 맛집"),
        _row("kakao", query="안산시 탕수육 맛집"),
    ]
    item = build_recommendation(cluster, "경기도", "안산시")
    assert item is not None
    assert item["rating"] == 0
    assert item["recommendation_label"] == "지역 반복 노출"
    assert len(item["evidence"]["specific_queries"]) >= 3


def test_single_cross_source_keyword_is_not_enough_by_itself():
    rows = [
        _row("kakao", query="안산시 짬뽕 맛집"),
        _row("naver", query="안산시 짬뽕 맛집"),
    ]
    assert build_recommendation(rows, "경기도", "안산시") is None


def test_weak_single_source_generic_result_does_not_pass():
    item = build_recommendation([_row("kakao", query="안산시 맛집")], "경기도", "안산시")
    assert item is None


def test_name_and_nearby_location_merge_across_sources():
    a = _row("kakao", name="백년교동짬뽕 안산장상동점", query="안산시 짬뽕 맛집")
    b = _row(
        "google",
        name="백년교동짬뽕 안산장상동점",
        query="안산시 중식 맛집",
        rating=4.3,
        user_rating_count=500,
    )
    assert same_place(a, b)
    result = merge_and_rank([a, b, _row("kakao", query="안산시 중국집")], "경기도", "안산시")
    assert result
    assert "kakao" in result[0]["sources"]
    assert "google" in result[0]["sources"]


def test_local_large_cafe_can_pass_without_google():
    name = "로컬대형카페"
    base = {
        "name": name,
        "category": "카페",
        "query_category": "카페",
        "address": "경기도 테스트시 테스트구",
        "road_address": "경기도 테스트시 테스트구",
    }
    cluster = [
        _row("kakao", query="테스트시 대형카페", city="테스트시", **base),
        _row("kakao", query="테스트시 베이커리카페", city="테스트시", **base),
        _row("kakao", query="테스트시 뷰카페", city="테스트시", **base),
    ]
    # build_recommendation receives the runtime city; no production region/store exception exists.
    item = build_recommendation(cluster, "경기도", "테스트시")
    assert item is not None
    assert item["cuisine"] == "카페"
    assert item["rating"] == 0
    assert item["recommendation_label"] == "지역 반복 노출"


def test_major_cafe_chain_is_not_recommended_even_with_many_hits():
    base = {
        "name": "스타벅스 테스트점",
        "category": "카페",
        "query_category": "카페",
    }
    cluster = [
        _row("kakao", query="테스트시 카페", **base),
        _row("kakao", query="테스트시 대형카페", **base),
        _row("google", query="테스트시 카페", rating=4.6, user_rating_count=1200, **base),
    ]
    assert build_recommendation(cluster, "경기도", "테스트시") is None


def test_spatial_generic_hit_alone_is_not_taste_evidence():
    item = build_recommendation(
        [_row(
            "kakao",
            name="무명카페",
            query="테스트시 카페 지역후보",
            category="카페",
            query_category="카페",
            discovery_mode="spatial_inventory",
        )],
        "경기도",
        "테스트시",
    )
    assert item is None


def test_google_nearby_high_volume_place_can_recommend_without_menu_keyword():
    item = build_recommendation(
        [_row(
            "google",
            name="로컬인기식당",
            query="테스트시 음식점 인기도 주변검색",
            rating=4.2,
            user_rating_count=783,
            discovery_mode="nearby_popularity",
        )],
        "경기도",
        "테스트시",
    )
    assert item is not None
    assert item["user_rating_count"] == 783
    assert item["evidence"]["specific_queries"] == []
