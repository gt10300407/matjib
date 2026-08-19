from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_exposes_background_refresh_contract():
    text = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "4.5.0"' in text
    assert '"/api/v1/region/refresh-async"' in text
    assert '"/api/v1/region/refresh-status"' in text
    assert "asyncio.create_task" in text
    assert "already_running" in text


def test_frontend_never_waits_on_legacy_sync_refresh_button():
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "app-fast-refresh.js").read_text(encoding="utf-8")
    assert "/static/app-fast-refresh.js" in index
    assert "/api/v1/region/refresh-async" in js
    assert "/api/v1/region/refresh-status" in js
    assert "stopImmediatePropagation" in js
    assert "폰을 계속 들고 기다릴 필요 없음" in js
