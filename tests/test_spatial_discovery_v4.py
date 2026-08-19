from backend.app.collectors.google_places import _grid_circles
import backend.app.collectors.kakao as kakao_module


def test_kakao_no_longer_does_exhaustive_spatial_inventory():
    assert not hasattr(kakao_module.KakaoCollector, "_grid_rects")
    assert "CATEGORY_URL" not in kakao_module.__dict__


def test_no_region_specific_query_map_in_production_collector():
    assert not hasattr(kakao_module, "REGION_SPECIAL_QUERY_SPECS")


def test_google_nearby_grid_has_bounded_generic_popularity_coverage():
    bbox = [126.70, 37.20, 127.00, 37.50]
    circles = _grid_circles(bbox, n=2)
    assert len(circles) == 4
    for lat, lon, radius in circles:
        assert bbox[1] <= lat <= bbox[3]
        assert bbox[0] <= lon <= bbox[2]
        assert 1200 <= radius <= 50000
