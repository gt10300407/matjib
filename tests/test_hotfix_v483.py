from backend.app import main


def test_region_handles_list_based_food_seed_without_500(monkeypatch):
    monkeypatch.setattr(main.refresh_service, "get_cached_restaurants", lambda *args, **kwargs: [])
    payload = main.region("전라남도", "곡성군", 10)
    assert payload["ok"] is True
    assert payload["restaurants"] == []
    assert payload["foods"] == []


def test_known_region_food_seed_is_filtered_from_list(monkeypatch):
    monkeypatch.setattr(main.refresh_service, "get_cached_restaurants", lambda *args, **kwargs: [])
    payload = main.region("경기도", "안산시", 10)
    assert payload["ok"] is True
    assert any(item["name"] == "대부도 바지락칼국수" for item in payload["foods"])


def test_source_status_does_not_call_settings_with_missing_argument():
    payload = main.sources_status()
    assert {"google", "kakao", "data_go", "excellent", "tourapi"} <= set(payload)


def test_stats_handles_list_based_food_seed(monkeypatch):
    monkeypatch.setattr(main.refresh_service, "count_verified", lambda *args, **kwargs: 0)
    payload = main.stats("전라남도", "곡성군")
    assert payload["restaurants"] == 0
    assert payload["representative_foods"] == 0


def test_consumer_info_layer_is_loaded():
    text = (main.FRONTEND / "app-refresh-async.js").read_text(encoding="utf-8")
    assert "/static/app-user-info.js" in text
