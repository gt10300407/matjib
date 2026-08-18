from backend.app.collectors.kakao import CAFE_QUERY_SPECS, REGION_SPECIAL_QUERY_SPECS, KakaoCollector


def test_cafe_discovery_has_multiple_intents():
    cafe_queries = [tpl for category, tpl, _ in CAFE_QUERY_SPECS if category == "카페"]
    assert len(cafe_queries) >= 10
    assert any("대형카페" in q for q in cafe_queries)
    assert any("베이커리카페" in q for q in cafe_queries)
    assert any("브런치카페" in q for q in cafe_queries)
    assert any("뷰카페" in q for q in cafe_queries)


def test_ansan_special_area_queries_include_daebudo():
    specs = REGION_SPECIAL_QUERY_SPECS.get("안산시", [])
    assert specs
    assert any("대부도" in tpl for _, tpl, _ in specs)


def test_cafe_spatial_grid_is_bounded_to_nine_calls():
    bbox = [126.5, 37.0, 127.0, 37.5]
    rects = KakaoCollector._grid_rects(bbox, n=3)
    assert len(rects) == 9
    assert rects[0][0] == bbox[0]
    assert rects[-1][2] == bbox[2]
    assert rects[-1][3] == bbox[3]
