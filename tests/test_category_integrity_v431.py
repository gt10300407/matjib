from backend.app.category_rules import resolve_cluster_cuisine
from backend.app.evidence import build_recommendation


def _google(name="테스트식당", *, query_category="분식", types=None, rating=4.5, reviews=100):
    return {
        "provider": "google",
        "provider_id": "g-1",
        "province": "경기도",
        "city": "안산시",
        "name": name,
        "category": query_category,
        "cuisine": query_category,
        "query_category": query_category,
        "query_text": f"안산시 {query_category} 맛집",
        "discovery_mode": "keyword",
        "address": "경기도 안산시 단원구 테스트로 1",
        "road_address": "경기도 안산시 단원구 테스트로 1",
        "x": "126.8",
        "y": "37.3",
        "rating": rating,
        "user_rating_count": reviews,
        "primary_type": (types or ["restaurant"])[0],
        "raw_json": {"types": types or ["restaurant"], "primaryType": (types or ["restaurant"])[0]},
    }


def test_query_category_never_becomes_cuisine_for_generic_google_restaurant():
    row = _google(query_category="분식", types=["restaurant"])
    cuisine, detail = resolve_cluster_cuisine([row])
    assert cuisine == "기타"
    assert cuisine != row["query_category"]
    assert "query" in detail["rule"]


def test_google_intrinsic_type_wins_even_when_discovered_by_wrong_query_bucket():
    row = _google(query_category="분식", types=["korean_restaurant", "restaurant"])
    cuisine, detail = resolve_cluster_cuisine([row])
    assert cuisine == "한식"
    assert any(s["reason"].startswith("google_type") for s in detail["signals"])


def test_recommendation_display_category_does_not_leak_from_search_query():
    row = _google(query_category="분식", types=["restaurant"], rating=4.9, reviews=178)
    item = build_recommendation([row], "경기도", "안산시")
    assert item is not None
    assert item["cuisine"] == "기타"
    assert item["cuisine"] != "분식"


def test_multi_source_consensus_can_override_one_provider_category():
    kakao = {
        "provider": "kakao",
        "provider_id": "k-1",
        "province": "경기도",
        "city": "안산시",
        "name": "개성손만두 테스트점",
        "road_address": "경기도 안산시 단원구 테스트로 7",
        "address": "경기도 안산시 단원구 테스트로 7",
        "raw_json": {"category_name": "음식점 > 분식", "category_group_code": "FD6"},
    }
    license_row = {
        "provider": "general",
        "provider_id": "p-1",
        "province": "경기도",
        "city": "안산시",
        "name": "개성손만두 테스트점",
        "road_address": "경기도 안산시 단원구 테스트로 7",
        "address": "경기도 안산시 단원구 테스트로 7",
        "raw_json": {"UPTAE_NM": "한식"},
    }
    cuisine, detail = resolve_cluster_cuisine([kakao, license_row])
    assert cuisine == "한식"
    assert detail["scores"]["한식"] > detail["scores"]["분식"]


def test_duplicate_query_hits_do_not_vote_a_cuisine_into_existence():
    a = _google(query_category="분식", types=["restaurant"])
    b = dict(a)
    b["query_category"] = "한식"
    b["query_text"] = "안산시 한식 맛집"
    cuisine, detail = resolve_cluster_cuisine([a, b])
    assert cuisine == "기타"
    assert detail["scores"] == {}
