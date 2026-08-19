from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_exposes_background_refresh_contract():
    text = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "4.6.0"' in text
    assert '"/api/v1/region/refresh-async"' in text
    assert '"/api/v1/region/refresh-status"' in text
    assert "asyncio.create_task" in text
    assert "already_running" in text


def test_frontend_never_waits_on_legacy_sync_refresh_button():
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "app-refresh-async.js").read_text(encoding="utf-8")
    assert "/static/app-refresh-async.js" in index
    assert "/static/app-fast-refresh.js" not in index
    assert "/api/v1/region/refresh-async" in js
    assert "/api/v1/region/refresh-status" in js
    assert "cloneNode(true)" in js
    assert "stopImmediatePropagation" in js
    assert "화면을 계속 써도 돼" in js
