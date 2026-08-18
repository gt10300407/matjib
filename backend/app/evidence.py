from __future__ import annotations

import hashlib
import math
import re
from difflib import SequenceMatcher

_GENERIC_QUERIES = {
    "맛집", "현지인 맛집", "오래된 맛집", "로컬 맛집",
    "카페", "음식점",
}

_MAJOR_CHAIN_TOKENS = {
    "스타벅스", "메가mgc커피", "메가커피", "빽다방", "컴포즈커피",
    "이디야", "투썸플레이스", "할리스", "폴바셋", "더벤티",
    "매머드커피", "매머드익스프레스", "커피빈", "파스쿠찌", "엔제리너스",
    "탐앤탐스", "카페베네", "공차", "파리바게뜨", "뚜레쥬르",
}


def normalize_name(value: str | None) -> str:
    text = (value or "").lower().strip()
    text = re.sub(r"[\s\-_/·ㆍ.,()\[\]{}'\"`]+", "", text)
    return text


def is_major_chain(value: str | None) -> bool:
    name = normalize_name(value)
    return any(token in name for token in _MAJOR_CHAIN_TOKENS)


def _address_tokens(value: str | None) -> set[str]:
    text = re.sub(r"[^0-9가-힣A-Za-z ]+", " ", value or "")
    return {x for x in text.split() if len(x) >= 2}


def _coords(row):
    try:
        x = float(row.get("x"))
        y = float(row.get("y"))
        if abs(x) <= 180 and abs(y) <= 90:
            return x, y
    except Exception:
        pass
    return None


def _distance_m(a, b) -> float | None:
    ca, cb = _coords(a), _coords(b)
    if not ca or not cb:
        return None
    lon1, lat1 = map(math.radians, ca)
    lon2, lat2 = map(math.radians, cb)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def same_place(a: dict, b: dict) -> bool:
    na, nb = normalize_name(a.get("name")), normalize_name(b.get("name"))
    if not na or not nb:
        return False
    if na == nb:
        return True

    dist = _distance_m(a, b)
    sim = SequenceMatcher(None, na, nb).ratio()
    if dist is not None and dist <= 350 and sim >= 0.66:
        return True

    at = _address_tokens(a.get("road_address") or a.get("address"))
    bt = _address_tokens(b.get("road_address") or b.get("address"))
    address_overlap = len(at & bt) >= 2
    contained = min(len(na), len(nb)) >= 5 and (na in nb or nb in na)
    return address_overlap and (sim >= 0.78 or contained)


def _specific_query(query: str | None, city: str) -> bool:
    q = (query or "").replace(city, "").strip()
    return bool(q and q not in _GENERIC_QUERIES)


def _google_component(rating: float, reviews: int) -> float:
    if rating <= 0 or reviews <= 0:
        return 0.0
    rating_pts = max(0.0, min(22.0, (rating - 3.5) / 1.5 * 22.0))
    review_pts = max(0.0, min(23.0, math.log10(reviews + 1) / 4.0 * 23.0))
    return rating_pts + review_pts


def build_recommendation(cluster: list[dict], province: str, city: str) -> dict | None:
    if not cluster:
        return None
    if any(is_major_chain(r.get("name")) for r in cluster):
        return None

    sources = sorted({str(r.get("provider") or "unknown") for r in cluster})
    queries = []
    keyword_queries = []
    categories = []
    discovery_modes = sorted({str(r.get("discovery_mode") or "unknown") for r in cluster})
    for row in cluster:
        q = (row.get("query_text") or "").strip()
        if q and q not in queries:
            queries.append(q)
        if row.get("discovery_mode") == "keyword" and q and q not in keyword_queries:
            keyword_queries.append(q)
        c = (row.get("query_category") or row.get("cuisine") or row.get("category") or "").strip()
        if c and c not in categories:
            categories.append(c)

    # Spatial/Nearby inventory exists to prevent false negatives. It must never be
    # mistaken for repeated taste evidence merely because grid cells overlap.
    specific_queries = [q for q in keyword_queries if _specific_query(q, city)]
    source_hits: dict[str, int] = {}
    keyword_source_hits: dict[str, int] = {}
    for row in cluster:
        provider = str(row.get("provider") or "unknown")
        source_hits[provider] = source_hits.get(provider, 0) + 1
        if row.get("discovery_mode") == "keyword":
            keyword_source_hits[provider] = keyword_source_hits.get(provider, 0) + 1

    google_rows = [r for r in cluster if r.get("provider") == "google"]
    google_best = max(
        google_rows,
        key=lambda r: (float(r.get("rating") or 0), int(r.get("user_rating_count") or 0)),
        default={},
    )
    rating = float(google_best.get("rating") or 0)
    reviews = int(google_best.get("user_rating_count") or 0)
    google_strong = (rating >= 4.4 and reviews >= 50) or (rating >= 4.2 and reviews >= 200)
    google_high_volume = rating >= 4.0 and reviews >= 500

    official = any(r.get("provider") == "excellent" or r.get("verified_public") for r in cluster)
    cross_source = len(sources) >= 2
    repeated_local = (
        keyword_source_hits.get("kakao", 0) >= 3
        or keyword_source_hits.get("naver", 0) >= 3
        or len(specific_queries) >= 3
    )

    # Discovery coverage and recommendation are intentionally separate:
    # - inventory/nearby can discover a place without knowing its menu keyword
    # - strong/high-volume user evaluation can recommend it
    # - repeated keyword evidence or official data can recommend it without Google
    eligible = google_strong or google_high_volume or repeated_local or official
    if not eligible:
        return None

    google_pts = _google_component(rating, reviews)
    query_pts = min(30.0, len(specific_queries) * 6.0 + min(6.0, max(0, len(keyword_queries) - len(specific_queries)) * 2.0))
    source_pts = min(15.0, max(0, len(sources) - 1) * 7.5)
    official_pts = 10.0 if official else 0.0
    score = round(min(100.0, google_pts + query_pts + source_pts + official_pts), 1)

    if official and cross_source:
        label = "공식정보+다중출처"
    elif google_strong and repeated_local:
        label = "평가+지역반복"
    elif google_high_volume and not google_strong:
        label = "다수평가 인기"
    elif repeated_local:
        label = "지역 반복 노출"
    elif google_strong:
        label = "사용자 평가 강함"
    else:
        label = "공식정보 확인"

    preferred = google_best or cluster[0]
    url_row = next((r for r in cluster if r.get("provider") == "kakao" and r.get("place_url")), None)
    if not url_row:
        url_row = next((r for r in cluster if r.get("place_url")), preferred)

    name = preferred.get("name") or cluster[0].get("name") or "이름없음"
    address = preferred.get("road_address") or preferred.get("address")
    if not address:
        address = next((r.get("road_address") or r.get("address") for r in cluster if r.get("road_address") or r.get("address")), None)

    cuisine = preferred.get("cuisine") or preferred.get("query_category") or preferred.get("category") or "기타"
    if cuisine in {"전체", "맛집", "restaurant", "음식점"}:
        cuisine = next((c for c in categories if c not in {"전체", "맛집", "음식점"}), "기타")

    evidence = {
        "sources": sources,
        "source_hits": source_hits,
        "keyword_source_hits": keyword_source_hits,
        "discovery_modes": discovery_modes,
        "queries": queries,
        "keyword_queries": keyword_queries,
        "specific_queries": specific_queries,
        "google": {
            "rating": rating,
            "user_rating_count": reviews,
            "strong": google_strong,
            "high_volume": google_high_volume,
        },
        "official_excellent": official,
        "score_components": {
            "google_user_evidence": round(google_pts, 1),
            "query_repetition": round(query_pts, 1),
            "source_diversity": round(source_pts, 1),
            "official_data": round(official_pts, 1),
        },
        "rule": "후보 발견은 지역 공간검색으로 넓게 하고, 추천은 사용자평가·키워드 반복·공식정보의 실제 근거로 별도 판정. 공간 셀 중복은 맛집 근거로 계산하지 않음",
    }

    canonical = hashlib.sha1(f"{province}|{city}|{normalize_name(name)}|{address or ''}".encode()).hexdigest()
    return {
        "provider": "aggregate",
        "provider_id": canonical,
        "province": province,
        "city": city,
        "name": name,
        "category": cuisine,
        "cuisine": cuisine,
        "business_type": preferred.get("primary_type") or preferred.get("business_type") or "restaurant",
        "primary_type": preferred.get("primary_type"),
        "address": address,
        "road_address": address,
        "phone": next((r.get("phone") for r in cluster if r.get("phone")), None),
        "x": preferred.get("x") or next((r.get("x") for r in cluster if r.get("x")), None),
        "y": preferred.get("y") or next((r.get("y") for r in cluster if r.get("y")), None),
        "place_url": url_row.get("place_url") if url_row else None,
        "status": "추천 맛집",
        "verified_public": official,
        "rating": rating,
        "user_rating_count": reviews,
        "taste_score": score,
        "query_hits": len(keyword_queries),
        "recommendation_label": label,
        "source_count": len(sources),
        "sources": sources,
        "evidence": evidence,
        "raw_json": {"evidence": evidence},
    }


def merge_and_rank(rows: list[dict], province: str, city: str) -> list[dict]:
    clusters: list[list[dict]] = []
    for row in rows:
        matched = None
        for cluster in clusters:
            if any(same_place(row, existing) for existing in cluster):
                matched = cluster
                break
        if matched is None:
            clusters.append([row])
        else:
            matched.append(row)

    recommendations = []
    for cluster in clusters:
        item = build_recommendation(cluster, province, city)
        if item:
            recommendations.append(item)
    recommendations.sort(
        key=lambda x: (
            float(x.get("taste_score") or 0),
            int(x.get("source_count") or 0),
            int(x.get("user_rating_count") or 0),
            int(x.get("query_hits") or 0),
        ),
        reverse=True,
    )
    return recommendations
