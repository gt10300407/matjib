from backend.app.collectors.publicdata import normalize_license_row
from backend.app.evidence import entity_match_score, same_place
from backend.app.services import _best_entity, _belongs_to_requested_region


def _row(name, address, provider="kakao", phone=None, x=None, y=None, city="안산시"):
    return {
        "provider": provider,
        "provider_id": f"{provider}:{name}:{address}",
        "province": "경기도",
        "city": city,
        "name": name,
        "address": address,
        "road_address": address,
        "phone": phone,
        "x": x,
        "y": y,
    }


def test_same_name_different_city_is_never_same_entity():
    a = _row("산골추어탕", "경기도 안산시 단원구 테스트로 1", city="안산시")
    b = _row("산골추어탕", "경기도 시흥시 군자동 22-2", city="시흥시")
    assert entity_match_score(a, b) < 0
    assert same_place(a, b) is False


def test_exact_name_with_same_road_number_matches_without_ai():
    a = _row("워크 안산", "경기도 안산시 상록구 수인로 921", provider="kakao")
    b = _row("워크 안산", "대한민국 경기도 안산시 상록구 수인로 921", provider="google")
    assert entity_match_score(a, b) >= 55
    assert same_place(a, b) is True


def test_exact_name_with_conflicting_address_is_not_blindly_merged():
    a = _row("동일상호", "경기도 안산시 단원구 중앙대로 10")
    b = _row("동일상호", "경기도 안산시 상록구 본오로 999", provider="google")
    assert entity_match_score(a, b) < 55
    assert same_place(a, b) is False


def test_public_cafe_and_bakery_rows_keep_distinct_license_source():
    raw = {
        "MNG_NO": "CAFE-1",
        "BPLC_NM": "워크 안산",
        "ROAD_NM_ADDR": "경기도 안산시 상록구 수인로 921",
        "SALS_STTS_NM": "영업/정상",
        "UPTAE_NM": "커피숍",
    }
    cafe = normalize_license_row(raw, "경기도", "안산시", "rest_cafe", "휴게음식점")
    bakery = normalize_license_row({**raw, "MNG_NO": "BAKERY-1"}, "경기도", "안산시", "bakery", "제과점")
    assert cafe["provider"] == "rest_cafe"
    assert cafe["business_type"] == "휴게음식점 인허가"
    assert bakery["provider"] == "bakery"
    assert bakery["business_type"] == "제과점 인허가"


def test_best_entity_prefers_exact_local_kakao_identity():
    rows = [
        _row("워크 안산", "경기도 안산시 상록구 수인로 921", provider="kakao"),
        _row("워크온(WorkOn)", "경기도 안산시 다른로 20", provider="google"),
    ]
    best = _best_entity(rows, "워크 안산", "안산시", None)
    assert best is not None
    assert best["provider"] == "kakao"
    assert best["name"] == "워크 안산"


def test_region_filter_rejects_google_result_from_neighboring_city():
    wrong = _row("워크 안산", "경기도 시흥시 군자동 22-2", provider="google", city="안산시")
    assert _belongs_to_requested_region(wrong, "안산시", None) is False
