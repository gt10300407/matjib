from __future__ import annotations

from collections import defaultdict
import re

CANONICAL_CUISINES = {"한식", "중식", "일식", "양식", "아시아", "분식", "카페", "디저트", "기타"}

GOOGLE_TYPE_TO_CUISINE = {
    "korean_restaurant": "한식",
    "korean_barbecue_restaurant": "한식",
    "chinese_restaurant": "중식",
    "chinese_noodle_restaurant": "중식",
    "dim_sum_restaurant": "중식",
    "japanese_restaurant": "일식",
    "sushi_restaurant": "일식",
    "ramen_restaurant": "일식",
    "tonkatsu_restaurant": "일식",
    "japanese_curry_restaurant": "일식",
    "japanese_izakaya_restaurant": "일식",
    "italian_restaurant": "양식",
    "french_restaurant": "양식",
    "american_restaurant": "양식",
    "western_restaurant": "양식",
    "steak_house": "양식",
    "pizza_restaurant": "양식",
    "asian_restaurant": "아시아",
    "thai_restaurant": "아시아",
    "vietnamese_restaurant": "아시아",
    "indian_restaurant": "아시아",
    "indonesian_restaurant": "아시아",
    "cafe": "카페",
    "coffee_shop": "카페",
    "tea_house": "카페",
    "bakery": "디저트",
    "dessert_shop": "디저트",
    "dessert_restaurant": "디저트",
    "cake_shop": "디저트",
    "pastry_shop": "디저트",
}

# Generic cuisine/menu lexicon only. No region or business names belong here.
_TEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("카페", ("카페", "커피", "로스터리", "coffee", "cafe")),
    ("디저트", ("베이커리", "제과", "디저트", "케이크", "도넛", "마카롱", "bakery", "dessert")),
    ("중식", ("중식", "중국", "짜장", "자장", "짬뽕", "탕수육", "마라", "훠궈", "양꼬치")),
    ("일식", ("일식", "일본", "스시", "초밥", "라멘", "돈카츠", "돈까스", "우동", "소바", "이자카야")),
    ("양식", ("양식", "이탈리안", "파스타", "스테이크", "피자", "프렌치", "브런치")),
    ("아시아", ("베트남", "태국", "인도", "인도네시아", "쌀국수", "팟타이", "커리")),
    # 만두 자체는 여러 국가 음식에 존재하므로 분식으로 분류하지 않는다.
    # 한국식 명칭이 명확한 경우에만 한식의 약한 보조신호로 사용한다.
    ("한식", ("한식", "국밥", "갈비", "삼겹", "냉면", "칼국수", "백반", "곱창", "족발", "보쌈", "감자탕", "설렁탕", "순대국", "해장국", "손만두", "개성만두", "만두전골")),
    ("분식", ("분식", "떡볶이", "김밥", "라볶이", "쫄면", "튀김", "순대")),
)

_GENERIC_LABELS = {
    "", "전체", "맛집", "음식점", "restaurant", "restaurants",
    "일반음식점", "휴게음식점", "제과점", "모범음식점", "기타",
}


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def infer_text_cuisine(value: str | None) -> tuple[str, int, str]:
    text = _norm(value)
    if not text:
        return "기타", 0, "empty"
    for cuisine, tokens in _TEXT_RULES:
        if any(token.lower() in text for token in tokens):
            return cuisine, 65, f"text:{cuisine}"
    return "기타", 0, "no_text_rule"


def google_type_cuisine(types: list[str] | tuple[str, ...] | None) -> tuple[str, int, str]:
    for place_type in list(types or []):
        cuisine = GOOGLE_TYPE_TO_CUISINE.get(str(place_type))
        if cuisine:
            return cuisine, 95, f"google_type:{place_type}"
    return "기타", 0, "google_generic_type"


def intrinsic_row_cuisine(row: dict) -> tuple[str, int, str]:
    """Return cuisine from provider-owned metadata, never from the search query.

    query_category/query_text describe *how we found* a place. They are evidence,
    not the place's cuisine. Falling back from a query such as '분식 맛집' to
    cuisine='분식' is therefore forbidden.
    """
    provider = str(row.get("provider") or "")
    raw = row.get("raw_json") or {}

    if provider == "google":
        types = list(raw.get("types") or [])
        primary = raw.get("primaryType") or row.get("primary_type")
        if primary and primary not in types:
            types.insert(0, primary)
        category, confidence, reason = google_type_cuisine(types)
        if confidence:
            return category, confidence, reason

    elif provider == "kakao":
        category_text = str(raw.get("category_name") or "")
        category, _, reason = infer_text_cuisine(category_text)
        if category != "기타":
            return category, 95, f"kakao_category:{reason}"
        group = str(raw.get("category_group_code") or "")
        if group == "CE7":
            return "카페", 90, "kakao_group:CE7"

    elif provider in {"general", "rest_cafe", "bakery", "excellent"}:
        category_text = " ".join(
            str(raw.get(key) or "")
            for key in ("UPTAE_NM", "PRINC_FD_KND", "FD_KND", "BSNSSP_NM")
        )
        category, _, reason = infer_text_cuisine(category_text)
        if category != "기타":
            return category, 90, f"license_category:{reason}"
        if provider == "bakery":
            return "디저트", 85, "license_type:bakery"

    # As a last provider-owned fallback, accept a canonical stored category only
    # when it is not one of the generic/search labels. This primarily supports
    # sources that do not expose their raw taxonomy in a consistent schema.
    stored = str(row.get("cuisine") or row.get("category") or "").strip()
    if stored in CANONICAL_CUISINES and stored not in _GENERIC_LABELS and provider not in {"google", "kakao"}:
        return stored, 55, "stored_intrinsic_category"

    return "기타", 0, "no_intrinsic_category"


def resolve_cluster_cuisine(cluster: list[dict]) -> tuple[str, dict]:
    """Resolve one display cuisine from source metadata + generic name semantics.

    Duplicate rows from the same provider/place are de-duplicated, so appearing in
    several search queries cannot vote a cuisine into existence. One low-confidence
    name signal is added as a tie-breaker/consensus signal.
    """
    scores: dict[str, int] = defaultdict(int)
    signals: list[dict] = []
    seen_entities: set[tuple[str, str]] = set()

    for row in cluster:
        provider = str(row.get("provider") or "unknown")
        provider_id = str(row.get("provider_id") or row.get("name") or "")
        entity_key = (provider, provider_id)
        if entity_key in seen_entities:
            continue
        seen_entities.add(entity_key)

        cuisine, confidence, reason = intrinsic_row_cuisine(row)
        if cuisine != "기타" and confidence > 0:
            scores[cuisine] += confidence
            signals.append({"source": provider, "cuisine": cuisine, "confidence": confidence, "reason": reason})

    # Business-name semantics are generic rules, never a per-store allowlist.
    names = [str(row.get("name") or "") for row in cluster if row.get("name")]
    if names:
        # Use the longest label because it tends to retain branch/menu qualifiers.
        name = max(names, key=len)
        cuisine, confidence, reason = infer_text_cuisine(name)
        if cuisine != "기타" and confidence > 0:
            scores[cuisine] += confidence
            signals.append({"source": "name", "cuisine": cuisine, "confidence": confidence, "reason": reason})

    if not scores:
        return "기타", {"scores": {}, "signals": signals, "rule": "intrinsic_only_no_query_fallback"}

    # Stable tie-break order favors provider consensus score first and broad food
    # categories over accidental query labels. Query category is never consulted.
    tie_order = {name: idx for idx, name in enumerate(("한식", "중식", "일식", "양식", "아시아", "분식", "카페", "디저트"))}
    winner = max(scores, key=lambda cuisine: (scores[cuisine], -tie_order.get(cuisine, 999)))
    return winner, {
        "scores": dict(scores),
        "signals": signals,
        "rule": "provider intrinsic taxonomy + generic name semantics; search query never classifies cuisine",
    }
