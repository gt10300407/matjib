from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_assets_are_loaded_by_index():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'name="viewport"' in html
    assert "viewport-fit=cover" in html
    assert '/static/responsive.css' in html
    assert '/static/app-mobile.js' in html


def test_responsive_breakpoints_keep_desktop_and_add_mobile_views():
    css = (ROOT / "frontend" / "responsive.css").read_text(encoding="utf-8")
    assert "@media (max-width:1199px)" in css
    assert "@media (max-width:767px)" in css
    assert ".app.mobile-view-map .panel" in css
    assert ".app.mobile-view-list .stage" in css
    assert ".filters" in css and "overflow-x:auto" in css


def test_mobile_controller_has_map_and_list_modes():
    js = (ROOT / "frontend" / "app-mobile.js").read_text(encoding="utf-8")
    assert "data-mobile-view=\"map\"" in js
    assert "data-mobile-view=\"list\"" in js
    assert "MutationObserver" in js
    assert "applyView('list'" in js


def test_mobile_map_uses_real_viewport_instead_of_desktop_700px_canvas():
    js = (ROOT / "frontend" / "app-mobile.js").read_text(encoding="utf-8")
    assert "responsiveMapDims" in js
    assert "getBoundingClientRect" in js
    assert "scaleMobileMapGroup('.province', 1.32)" in js
    assert "scaleMobileMapGroup('.municipality', 1.22)" in js
