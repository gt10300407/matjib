from backend.app.collectors.google_places import _grid_circles
from backend.app.collectors.kakao import KakaoCollector
import backend.app.collectors.kakao as kakao_module


def test_kakao_grid_is_generic_and_covers_requested_bbox():
    bbox = [126.70, 37.20, 127.00, 37.50]
    rects = KakaoCollector._grid_rects(bbox, n=4)
    assert len(rects) == 16
    assert rects[0][0] == bbox[0]
    assert rects[-1][2] == bbox[2]
    assert rects[-1][3] == bbox[3]


def test_default_kakao_grid_is_four_by_four():
    bbox = [126.70, 37.20, 127.00, 37.50]
    assert len(KakaoCollector._grid_rects(bbox)) == 16


def test_no_region_specific_query_map_in_production_collector():
    assert not hasattr(kakao_module, "REGION_SPECIAL_QUERY_SPECS")


def test_google_nearby_grid_has_bounded_generic_coverage():
    bbox = [126.70, 37.20, 127.00, 37.50]
    circles = _grid_circles(bbox, n=2)
    assert len(circles) == 4
    for lat, lon, radius in circles:
        assert bbox[1] <= lat <= bbox[3]
        assert bbox[0] <= lon <= bbox[2]
        assert 1200 <= radius <= 50000
