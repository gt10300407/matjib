from __future__ import annotations

import hashlib
import math
import re
from difflib import SequenceMatcher

from .category_rules import resolve_cluster_cuisine

_GENERIC_QUERIES = {"맛집", "현지인 맛집", "오래된 맛집", "로컬 맛집", "카페", "음식점"}
_LICENSE_PROVIDERS = {"general", "rest_cafe", "bakery"}
_MAJOR_CHAIN_TOKENS = {
    "스타벅스", "메가mgc커피", "메가커피", "빽다방", "컴포즈커피", "이디야", "투썸플레이스",
    "할리스", "폴바셋", "더벤티", "매머드커피", "매머드익스프레스", "커피빈", "파스쿠찌",
    "엔제리너스", "탐앤탐스", "카페베네", "공차", "파리바게뜨", "뚜레쥬르",
}


def normalize_name(value: str | None) -> str:
    text = (value or "").lower().strip()
    return re.sub(r"[\s\-_/·ㆍ.,()\[\]{}'\"`]+", "", text)


def is_major_chain(value: str | None) -> bool:
    name = normalize_name(value)
    return any(token in name for token in _MAJOR_CHAIN_TOKENS)


def _phone_key(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _address_tokens(value: str | None) -> set[str]:
    text = re.sub(r"[^0-9가-힣A-Za-z ]+", " ", value or "")
    return {x.lower() for x in text.split() if len(x) >= 2}


def _normalized_address(value: str | None) -> str:
    return re.sub(r"[^0-9가-힣a-z]+", "", (value or "").lower())


def _road_number(value: str | None) -> str | None:
    text = value or ""
    nums = re.findall(r"(?<!\d)(\d+(?:-\d+)?)(?!\d)", text)
    return nums[-1] if nums else None


def _road_block_tokens(value: str | None) -> list[str]:
    """Return selective address tokens suitable for a blocking index.

    Province/city/district tokens are shared by thousands of businesses and would
    recreate the old quadratic scan. Prefer actual road-name tokens; otherwise use
    a few longest non-administrative tokens.
    """
    tokens = list(_address_tokens(value))
    road = [t for t in tokens if re.search(r"(?:대로|로|길)$", t)]
    if road:
        return sorted(road, key=len, reverse=True)[:2]
    non_admin = [t for t in tokens if not re.search(r"(?:도|시|군|구|읍|면|동|리)$", t)]
    return sorted(non_admin, key=len, reverse=True)[:2]


def _coords(row):
    try:
        x = float(row.get("x")); y = float(row.get("y"))
        if abs(x) <= 180 and abs(y) <= 90:
            return x, y
    except Exception:
        pass
    return None


def _distance_m(a, b) -> float | None:
    ca, cb = _coords(a), _coords(b)
    if not ca or not cb:
        return None
    lon1, lat1 = map(math.radians, ca); lon2, lat2 = map(math.radians, cb)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def entity_match_score(a: dict, b: dict) -> float:
    """Deterministic identity score. No AI, no taste signal.

    Signals are intentionally factual: region, normalized business name, phone,
    road-address tokens/number and trustworthy WGS84 distance. License coordinates
    are excluded upstream because LOCALDATA uses EPSG:5174.
    """
    city_a, city_b = str(a.get("city") or "").strip(), str(b.get("city") or "").strip()
    if city_a and city_b and city_a != city_b:
        return -100.0

    na, nb = normalize_name(a.get("name")), normalize_name(b.get("name"))
    if not na or not nb:
        return -100.0

    score = 0.0
    sim = SequenceMatcher(None, na, nb).ratio()
    contained = min(len(na), len(nb)) >= 4 and (na in nb or nb in na)
    if na == nb:
        score += 45
    elif contained:
        score += 32
    elif sim >= 0.92:
        score += 30
    elif sim >= 0.82:
        score += 22
    elif sim >= 0.70:
        score += 12
    else:
        score -= 15

    pa, pb = _phone_key(a.get("phone")), _phone_key(b.get("phone"))
    if len(pa) >= 8 and len(pb) >= 8:
        if pa == pb:
            score += 50
        else:
            score -= 30

    aa = a.get("road_address") or a.get("address")
    ab = b.get("road_address") or b.get("address")
    at, bt = _address_tokens(aa), _address_tokens(ab)
    if at and bt:
        overlap = len(at & bt)
        if overlap >= 4:
            score += 35
        elif overlap >= 2:
            score += 25
        elif overlap == 1:
            score += 8
        else:
            score -= 20
        ra, rb = _road_number(aa), _road_number(ab)
        if ra and rb:
            if ra == rb:
                score += 20
            elif overlap >= 2:
                score -= 20

    dist = _distance_m(a, b)
    if dist is not None:
        if dist <= 50:
            score += 40
        elif dist <= 120:
            score += 30
        elif dist <= 350:
            score += 15
        elif dist >= 1500:
            score -= 35

    return score


def same_place(a: dict, b: dict) -> bool:
    return entity_match_score(a, b) >= 55


def _specific_query(query: str | None, city: str) -> bool:
    q = (query or "").replace(city, "").strip()
    return bool(q and q not in _GENERIC_QUERIES)


def _google_component(rating: float, reviews: int) -> float:
    if rating <= 0 or reviews <= 0:
        return 0.0
    rating_pts = max(0.0, min(22.0, (rating - 3.5) / 1.5 * 22.0))
    review_pts = max(0.0, min(23.0, math.log10(reviews + 1) / 4.0 * 23.0))
    return rating_pts + review_pts


def _identity_preferred(cluster: list[dict], google_best: dict) -> dict:
    # Korean local POI / official license master owns identity; Google owns rating evidence.
    for provider in ("kakao", "general", "rest_cafe", "bakery", "excellent"):
        row = next((r for r in cluster if r.get("provider") == provider), None)
        if row:
            return row
    return google_best or cluster[0]


def build_recommendation(cluster: list[dict], province: str, city: str) -> dict | None:
    if not cluster or any(is_major_chain(r.get("name")) for r in cluster):
        return None

    sources = sorted({str(r.get("provider") or "unknown") for r in cluster})
    taste_sources = [s for s in sources if s not in _LICENSE_PROVIDERS]
    queries, keyword_queries = [], []
    discovery_modes = sorted({str(r.get("discovery_mode") or "unknown") for r in cluster})
    for row in cluster:
        q = (row.get("query_text") or "").strip()
        if q and q not in queries:
            queries.append(q)
        if row.get("discovery_mode") == "keyword" and q and q not in keyword_queries:
            keyword_queries.append(q)

    specific_queries = [q for q in keyword_queries if _specific_query(q, city)]
    source_hits, keyword_source_hits = {}, {}
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
    rating = float(google_best.get("rating") or 0); reviews = int(google_best.get("user_rating_count") or 0)
    google_strong = (rating >= 4.4 and reviews >= 50) or (rating >= 4.2 and reviews >= 200)
    google_high_volume = rating >= 4.0 and reviews >= 500

    official = any(r.get("provider") == "excellent" or r.get("verified_public") for r in cluster)
    repeated_local = (
        keyword_source_hits.get("kakao", 0) >= 3
        or keyword_source_hits.get("naver", 0) >= 3
        or len(specific_queries) >= 3
    )

    # Official/licensing status is factual trust evidence, not proof that a place is tasty.
    # It may boost an already-qualified recommendation but can never qualify by itself.
    eligible = google_strong or google_high_volume or repeated_local
    if not eligible:
        return None

    google_pts = _google_component(rating, reviews)
    query_pts = min(30.0, len(specific_queries) * 6.0 + min(6.0, max(0, len(keyword_queries) - len(specific_queries)) * 2.0))
    source_pts = min(15.0, max(0, len(taste_sources) - 1) * 7.5)
    official_pts = 10.0 if official else 0.0
    score = round(min(100.0, google_pts + query_pts + source_pts + official_pts), 1)

    if google_strong and repeated_local:
        label = "평가+지역반복"
    elif google_high_volume and not google_strong:
        label = "다수평가 인기"
    elif repeated_local:
        label = "지역 반복 노출"
    else:
        label = "사용자 평가 강함"

    preferred = _identity_preferred(cluster, google_best)
    url_row = next((r for r in cluster if r.get("provider") == "kakao" and r.get("place_url")), None)
    if not url_row:
        url_row = next((r for r in cluster if r.get("place_url")), google_best or preferred)

    name = preferred.get("name") or cluster[0].get("name") or "이름없음"
    address = preferred.get("road_address") or preferred.get("address")
    if not address:
        address = next((r.get("road_address") or r.get("address") for r in cluster if r.get("road_address") or r.get("address")), None)

    # Critical invariant: search query category is discovery evidence only.
    # The displayed cuisine must come from provider-owned taxonomy/metadata or a
    # generic business-name rule, never from the query that happened to find it.
    cuisine, category_resolution = resolve_cluster_cuisine(cluster)

    evidence = {
        "sources": sources,
        "taste_sources": taste_sources,
        "license_sources": [s for s in sources if s in _LICENSE_PROVIDERS],
        "source_hits": source_hits,
        "keyword_source_hits": keyword_source_hits,
        "discovery_modes": discovery_modes,
        "queries": queries,
        "keyword_queries": keyword_queries,
        "specific_queries": specific_queries,
        "google": {"rating": rating, "user_rating_count": reviews, "strong": google_strong, "high_volume": google_high_volume},
        "official_excellent": official,
        "category_resolution": category_resolution,
        "score_components": {
            "google_user_evidence": round(google_pts, 1), "query_repetition": round(query_pts, 1),
            "source_diversity": round(source_pts, 1), "official_data": round(official_pts, 1),
        },
        "rule": "업체 동일성은 이름·주소·전화·좌표로 판정하고, 음식 분류는 공급자 고유 분류/상호 규칙으로 결정. 공공·모범음식점 정보는 존재/신뢰 보강용이며 맛집 추천 자격을 단독으로 만들지 않음.",
    }

    canonical = hashlib.sha1(f"{province}|{city}|{normalize_name(name)}|{address or ''}".encode()).hexdigest()
    return {
        "provider": "aggregate", "provider_id": canonical, "province": province, "city": city,
        "name": name, "category": cuisine, "cuisine": cuisine,
        "business_type": preferred.get("primary_type") or preferred.get("business_type") or "restaurant",
        "primary_type": preferred.get("primary_type"), "address": address, "road_address": address,
        "phone": next((r.get("phone") for r in cluster if r.get("phone")), None),
        "x": next((r.get("x") for r in cluster if _coords(r)), preferred.get("x")),
        "y": next((r.get("y") for r in cluster if _coords(r)), preferred.get("y")),
        "place_url": url_row.get("place_url") if url_row else None, "status": "추천 맛집",
        "verified_public": official, "rating": rating, "user_rating_count": reviews, "taste_score": score,
        "query_hits": len(keyword_queries), "recommendation_label": label, "source_count": len(taste_sources),
        "sources": sources, "evidence": evidence, "raw_json": {"evidence": evidence},
    }


def _blocking_keys(row: dict, *, lookup: bool = False) -> set[str]:
    """Generate selective candidates before the expensive identity score.

    The old algorithm compared every row with every cluster. Broad name-prefix or
    city/address buckets can silently recreate that O(n²) behavior, so this index
    only uses strong/selective identity signals. The unchanged entity_match_score
    remains the final decision rule.
    """
    keys: set[str] = set()
    provider = str(row.get("provider") or "")
    provider_id = str(row.get("provider_id") or "")
    if provider and provider_id:
        keys.add(f"pid:{provider}:{provider_id}")

    name = normalize_name(row.get("name"))
    if name:
        # Normalization already handles whitespace/punctuation variants, which is
        # the common duplicate shape within one provider/search family.
        keys.add(f"name:{name}")

    phone = _phone_key(row.get("phone"))
    if len(phone) >= 8:
        keys.add(f"phone:{phone}")

    address = row.get("road_address") or row.get("address")
    exact_address = _normalized_address(address)
    if len(exact_address) >= 5:
        keys.add(f"addr:{exact_address}")
    road_no = _road_number(address)
    if road_no:
        for token in _road_block_tokens(address):
            keys.add(f"road:{token}:{road_no}")

    coord = _coords(row)
    if coord:
        lon, lat = coord
        size = 0.0015  # ~130-170m per cell in Korea
        gx, gy = int(math.floor(lon / size)), int(math.floor(lat / size))
        if lookup:
            # ±3 cells preserves the final scorer's <=350m candidate window even
            # near cell boundaries. Only populated cells produce comparisons.
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    keys.add(f"geo:{gx+dx}:{gy+dy}")
        else:
            keys.add(f"geo:{gx}:{gy}")
    return keys


def merge_and_rank(rows: list[dict], province: str, city: str) -> list[dict]:
    clusters: list[list[dict]] = []
    block_index: dict[str, set[int]] = {}

    def index_row(row: dict, cluster_id: int):
        for key in _blocking_keys(row, lookup=False):
            block_index.setdefault(key, set()).add(cluster_id)

    for row in rows:
        candidate_ids: set[int] = set()
        for key in _blocking_keys(row, lookup=True):
            candidate_ids.update(block_index.get(key, ()))

        matched_id = None
        best_score = -101.0
        for cluster_id in candidate_ids:
            cluster = clusters[cluster_id]
            cluster_score = max((entity_match_score(row, existing) for existing in cluster), default=-100.0)
            if cluster_score >= 55 and cluster_score > best_score:
                matched_id, best_score = cluster_id, cluster_score

        if matched_id is None:
            matched_id = len(clusters)
            clusters.append([row])
        else:
            clusters[matched_id].append(row)
        index_row(row, matched_id)

    recommendations = []
    for cluster in clusters:
        item = build_recommendation(cluster, province, city)
        if item:
            recommendations.append(item)
    recommendations.sort(
        key=lambda x: (
            float(x.get("taste_score") or 0), int(x.get("source_count") or 0),
            int(x.get("user_rating_count") or 0), int(x.get("query_hits") or 0),
        ), reverse=True,
    )
    return recommendations
