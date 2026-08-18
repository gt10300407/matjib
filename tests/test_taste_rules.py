from backend.app.collectors.google_places import _bayesian_score, _classify, _is_verified


def test_precision_first_verification_rules():
    assert _is_verified(4.4, 50) is True
    assert _is_verified(4.2, 200) is True
    assert _is_verified(5.0, 5) is False
    assert _is_verified(4.1, 5000) is False


def test_bayesian_score_penalizes_tiny_samples():
    assert _bayesian_score(5.0, 5) < _bayesian_score(4.6, 1000)


def test_cuisine_classification():
    assert _classify(["korean_restaurant", "restaurant"], "전체") == "한식"
    assert _classify(["japanese_restaurant", "restaurant"], "전체") == "일식"
    assert _classify(["cafe", "food"], "전체") == "카페"
    assert _classify(["restaurant"], "중식") == "중식"
