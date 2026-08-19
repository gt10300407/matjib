from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_consumer_ui_collapses_to_one_taste_list():
    js = (ROOT / "frontend" / "app-refresh-async.js").read_text(encoding="utf-8")
    assert "this product is one regional 맛집 list" in js
    assert "hide(document.getElementById('filters'))" in js
    assert "hide(document.getElementById('foods')?.closest('section'))" in js
    assert "hide(document.querySelector('.panelScroll .summary'))" in js
    assert "이 지역 맛집" in js
    assert "activeRestaurantFilter = '전체'" in js


def test_consumer_cards_hide_cuisine_labels_but_keep_internal_metadata():
    js = (ROOT / "frontend" / "app-refresh-async.js").read_text(encoding="utf-8")
    taste = (ROOT / "frontend" / "app-taste.js").read_text(encoding="utf-8")
    assert "stripCategoryLabels" in js
    assert "menu.textContent = parts.slice(1).join(' · ')" in js
    # Backend/frontend recommendation metadata is intentionally not destroyed;
    # only consumer category navigation/labels are removed.
    assert "r.cuisine" in taste
