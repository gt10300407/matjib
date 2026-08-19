from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_does_not_draw_fallback_before_geojson_is_ready():
    text = (ROOT / "frontend" / "app-mobile.js").read_text(encoding="utf-8")
    assert "if (!provinces?.features?.length) return;" in text
    assert "applyView('map', { redraw: false })" in text


def test_mobile_motion_is_minimized():
    text = (ROOT / "frontend" / "app-mobile.js").read_text(encoding="utf-8")
    assert "globalTimeline.timeScale(1000)" in text
    assert "behavior: 'auto'" in text
    assert "animation-duration:.001ms" in text
    assert "transition-duration:.001ms" in text


def test_mobile_map_scaling_is_applied_without_extra_animation_frame():
    text = (ROOT / "frontend" / "app-mobile.js").read_text(encoding="utf-8")
    korea_wrapper = text.split("drawKorea = function responsiveDrawKorea", 1)[1].split("};", 1)[0]
    province_wrapper = text.split("drawProvince = function responsiveDrawProvince", 1)[1].split("};", 1)[0]
    assert "requestAnimationFrame" not in korea_wrapper
    assert "requestAnimationFrame" not in province_wrapper
