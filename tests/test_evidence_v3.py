from backend.app.evidence import build_recommendation, merge_and_rank, same_place


def _row(provider, name="백년교동짬뽕 안산장상동점", query="안산시 짬뽕 맛집", **extra):
    row = {
        "provider": provider,
        "provider_id": f"{provider}:{query}",
        "province": "경기도",
        "city": "안산시",
        "name": name,
        "category": "중식",
        "query_category": "중식",
        "query_text": query,
        "query_hits": 1,
        "address": "경기도 안산시 상록구 양달말길 5",
        "road_address": "경기도 안산시 상록구 양달말길 5",
        "x": "126.87",
        "y": "37.32",
    }
    row.update(extra)
    return row


def test_local_famous_place_can_pass_without_google():
    # Regression: a real local-famous restaurant must not disappear merely because
    # Google does not surface it. Repeated menu-level Kakao discovery is enough to
    # keep it in the recommendation set, with transparent local-search evidence.
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


def test_cross_source_candidate_passes_without_google_rating():
    rows = [
        _row("kakao", query="안산시 짬뽕 맛집"),
        _row("naver", query="안산시 짬뽕 맛집"),
    ]
    item = build_recommendation(rows, "경기도", "안산시")
    assert item is not None
    assert item["source_count"] == 2


def test_weak_single_source_generic_result_does_not_pass():
    item = build_recommendation([_row("kakao", query="안산시 맛집")], "경기도", "안산시")
    assert item is None


def test_name_and_nearby_location_merge_across_sources():
    a = _row("kakao", name="백년교동짬뽕 안산장상동점", query="안산시 짬뽕 맛집")
    b = _row("google", name="백년교동짬뽕 안산장상동점", query="안산시 중식 맛집", rating=4.1, user_rating_count=20)
    assert same_place(a, b)
    result = merge_and_rank([a, b, _row("kakao", query="안산시 중국집")], "경기도", "안산시")
    assert result
    assert "kakao" in result[0]["sources"]
    assert "google" in result[0]["sources"]
