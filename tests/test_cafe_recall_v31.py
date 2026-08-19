from backend.app.collectors.kakao import POPULARITY_QUERY_SPECS
import backend.app.collectors.kakao as kakao_module


def test_cafe_discovery_is_broad_popularity_first():
    cafe_queries = [tpl for category, tpl, _ in POPULARITY_QUERY_SPECS if category == "카페"]
    assert cafe_queries == ["{city} 인기 카페", "{city} 유명 카페"]


def test_production_collector_has_no_region_specific_query_map():
    assert not hasattr(kakao_module, "REGION_SPECIAL_QUERY_SPECS")


def test_v47_removes_exhaustive_spatial_grid_inventory():
    assert not hasattr(kakao_module.KakaoCollector, "_grid_rects")
