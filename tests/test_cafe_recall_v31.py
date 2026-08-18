from backend.app.collectors.kakao import CAFE_QUERY_SPECS, KakaoCollector
import backend.app.collectors.kakao as kakao_module


def test_cafe_discovery_has_multiple_generic_intents():
    cafe_queries = [tpl for category, tpl, _ in CAFE_QUERY_SPECS if category == "카페"]
    assert len(cafe_queries) >= 10
    assert any("대형카페" in q for q in cafe_queries)
    assert any("베이커리카페" in q for q in cafe_queries)
    assert any("브런치카페" in q for q in cafe_queries)
    assert any("뷰카페" in q for q in cafe_queries)


def test_production_collector_has_no_region_specific_query_map():
    assert not hasattr(kakao_module, "REGION_SPECIAL_QUERY_SPECS")


def test_spatial_grid_is_generic_sixteen_cell_coverage():
    bbox = [126.5, 37.0, 127.0, 37.5]
    rects = KakaoCollector._grid_rects(bbox, n=4)
    assert len(rects) == 16
    assert rects[0][0] == bbox[0]
    assert rects[-1][2] == bbox[2]
    assert rects[-1][3] == bbox[3]
