from __future__ import annotations

import asyncio
import math
import os
from typing import Any

import httpx

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.rating", "places.userRatingCount", "places.primaryType", "places.types",
    "places.googleMapsUri", "places.businessStatus",
])

# Explicit taste/popularity intents only. No cuisine/menu buckets.
SEARCH_SPECS = [
    ("전체", "{city} 맛집", "restaurant"),
    ("전체", "{city} 유명 맛집", "restaurant"),
    ("카페", "{city} 인기 카페", "cafe"),
    ("카페", "{city} 유명 카페", "cafe"),
]

TYPE_TO_CUISINE = {
    "korean_restaurant": "한식", "korean_barbecue_restaurant": "한식",
    "chinese_restaurant": "중식", "chinese_noodle_restaurant": "중식", "dim_sum_restaurant": "중식",
    "japanese_restaurant": "일식", "sushi_restaurant": "일식", "ramen_restaurant": "일식",
    "tonkatsu_restaurant": "일식", "japanese_curry_restaurant": "일식", "japanese_izakaya_restaurant": "일식",
    "italian_restaurant": "양식", "french_restaurant": "양식", "american_restaurant": "양식",
    "western_restaurant": "양식", "steak_house": "양식", "pizza_restaurant": "양식",
    "asian_restaurant": "아시아", "thai_restaurant": "아시아", "vietnamese_restaurant": "아시아",
    "indian_restaurant": "아시아", "indonesian_restaurant": "아시아",
    "cafe": "카페", "coffee_shop": "카페", "tea_house": "카페",
    "bakery": "디저트", "dessert_shop": "디저트", "dessert_restaurant": "디저트",
    "cake_shop": "디저트", "pastry_shop": "디저트",
}


def _valid_bbox(bbox):
    if not bbox or len(bbox) != 4:
        return False
    try:
        minx, miny, maxx, maxy = map(float, bbox)
    except Exception:
        return False
    return -180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90


def _bayesian_score(rating: float, reviews: int, prior: float = 4.2, weight: int = 100) -> float:
    if reviews <= 0:
        return 0.0
    return (reviews / (reviews + weight)) * rating + (weight / (reviews + weight)) * prior


def _is_verified(rating: float, reviews: int) -> bool:
    return (rating >= 4.4 and reviews >= 50) or (rating >= 4.2 and reviews >= 200)


def _classify(types: list[str], query_category: str = "전체") -> str:
    for t in types:
        if t in TYPE_TO_CUISINE:
            return TYPE_TO_CUISINE[t]
    return query_category if query_category != "전체" else "기타"


def _is_mass_market_fast_food(p: dict) -> bool:
    """Taxonomy-based automatic-recommendation exclusion, not a brand list."""
    types = set(p.get("types") or [])
    primary = str(p.get("primaryType") or "")
    return primary == "fast_food_restaurant" or "fast_food_restaurant" in types


def _grid_circles(bbox, n: int = 2):
    """Backward-compatible helper retained for tests/tools.

    v4.7.1 automatic recommendations no longer use Nearby POPULARITY because it
    measures generic place popularity and can surface mass-market chains as 맛집.
    """
    minx, miny, maxx, maxy = map(float, bbox)
    dx = (maxx - minx) / n
    dy = (maxy - miny) / n
    circles = []
    for ix in range(n):
        for iy in range(n):
            lon = minx + dx * (ix + 0.5)
            lat = miny + dy * (iy + 0.5)
            lat_km = dy * 111.0 / 2
            lon_km = dx * 111.0 * max(0.2, math.cos(math.radians(lat))) / 2
            radius_m = min(50000.0, max(1200.0, math.hypot(lat_km, lon_km) * 1000 * 1.08))
            circles.append((lat, lon, radius_m))
    return circles


class GooglePlacesCollector:
    def __init__(self):
        self.key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        self.api_calls = 0

    @property
    def enabled(self):
        return bool(self.key)

    def _headers(self):
        return {
            "X-Goog-Api-Key": self.key,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        }

    def _normalize_place(self, p: dict, province: str, city: str, query_text: str, discovery_mode: str):
        if p.get("businessStatus") not in (None, "OPERATIONAL"):
            return None
        rating = float(p.get("rating") or 0)
        reviews = int(p.get("userRatingCount") or 0)
        types = list(p.get("types") or [])
        loc = p.get("location") or {}
        cuisine = _classify(types)
        return {
            "provider": "google",
            "provider_id": str(p.get("id") or ""),
            "province": province,
            "city": city,
            "name": ((p.get("displayName") or {}).get("text") or "이름없음"),
            "category": cuisine,
            "cuisine": cuisine,
            "business_type": p.get("primaryType") or "restaurant",
            "primary_type": p.get("primaryType"),
            "address": p.get("formattedAddress"),
            "road_address": p.get("formattedAddress"),
            "phone": None,
            "x": str(loc.get("longitude")) if loc.get("longitude") is not None else None,
            "y": str(loc.get("latitude")) if loc.get("latitude") is not None else None,
            "place_url": p.get("googleMapsUri"),
            "status": "Google 검색 노출",
            "verified_public": False,
            "rating": rating,
            "user_rating_count": reviews,
            "taste_score": round(_bayesian_score(rating, reviews), 4),
            "query_category": "전체",
            "query_text": query_text,
            "query_hits": 1,
            "discovery_mode": discovery_mode,
            "raw_json": p,
        }

    async def _post(self, client: httpx.AsyncClient, url: str, body: dict):
        # Paid API: deliberately no automatic retry. A transient error must not silently bill extra calls.
        self.api_calls += 1
        r = await client.post(url, headers=self._headers(), json=body)
        if r.status_code != 200:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:500]
            raise RuntimeError(f"Google Places HTTP {r.status_code}: {detail}")
        return r.json()

    async def _search_one(self, client: httpx.AsyncClient, province: str, city: str, bbox, spec):
        _, query_tpl, included_type = spec
        query = query_tpl.format(city=city)
        body: dict[str, Any] = {
            "textQuery": query,
            "languageCode": "ko",
            "regionCode": "KR",
            "rankPreference": "RELEVANCE",
            "pageSize": 20,
        }
        if included_type:
            body["includedType"] = included_type
        if _valid_bbox(bbox):
            minx, miny, maxx, maxy = map(float, bbox)
            body["locationRestriction"] = {
                "rectangle": {
                    "low": {"latitude": miny, "longitude": minx},
                    "high": {"latitude": maxy, "longitude": maxx},
                }
            }
        data = await self._post(client, TEXT_SEARCH_URL, body)
        out = []
        for p in data.get("places", []):
            if _is_mass_market_fast_food(p):
                continue
            row = self._normalize_place(p, province, city, query, "keyword")
            if row:
                out.append(row)
        return out

    async def search_direct(self, province: str, city: str, query: str, bbox=None):
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0}
        q = " ".join(x for x in (city.strip(), query.strip()) if x)
        if not q:
            return [], {"candidate_count": 0, "api_calls": 0}
        body: dict[str, Any] = {
            "textQuery": q,
            "languageCode": "ko",
            "regionCode": "KR",
            "rankPreference": "RELEVANCE",
            "pageSize": 10,
        }
        if _valid_bbox(bbox):
            minx, miny, maxx, maxy = map(float, bbox)
            body["locationRestriction"] = {
                "rectangle": {
                    "low": {"latitude": miny, "longitude": minx},
                    "high": {"latitude": maxy, "longitude": maxx},
                }
            }
        before = self.api_calls
        timeout = httpx.Timeout(10.0, connect=4.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            data = await self._post(client, TEXT_SEARCH_URL, body)
        out = []
        for p in data.get("places", []):
            row = self._normalize_place(p, province, city, q, "direct_search")
            if row:
                out.append(row)
        return out, {"candidate_count": len(out), "api_calls": self.api_calls - before, "query": q}

    async def _nearby_one(self, client: httpx.AsyncClient, province: str, city: str, circle, group: str):
        """Retained for diagnostics/backward compatibility; not used by automatic refresh."""
        lat, lon, radius = circle
        included = ["restaurant"] if group == "음식점" else ["cafe", "coffee_shop", "bakery"]
        body = {
            "includedTypes": included,
            "maxResultCount": 20,
            "rankPreference": "POPULARITY",
            "languageCode": "ko",
            "regionCode": "KR",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": radius,
                }
            },
        }
        data = await self._post(client, NEARBY_SEARCH_URL, body)
        out = []
        for p in data.get("places", []):
            if _is_mass_market_fast_food(p):
                continue
            row = self._normalize_place(p, province, city, f"{city} {group} 인기도 주변검색", "nearby_popularity")
            if row:
                out.append(row)
        return out

    async def collect_candidates(self, province: str, city: str, bbox=None):
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0}
        self.api_calls = 0
        timeout = httpx.Timeout(12.0, connect=4.0)
        sem = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async def run_text(spec):
                async with sem:
                    return await self._search_one(client, province, city, bbox, spec)

            batches = await asyncio.gather(*(run_text(spec) for spec in SEARCH_SPECS))

        rows = [r for batch in batches for r in batch]
        return rows, {
            "candidate_count": len(rows),
            "api_calls": self.api_calls,
            "text_queries": len(SEARCH_SPECS),
            "nearby_popularity_calls": 0,
            "discovery_definition": "4 explicit taste/popularity Text Searches; Nearby-only popularity excluded",
        }

    async def collect_verified(self, province: str, city: str, bbox=None):
        rows, meta = await self.collect_candidates(province, city, bbox)
        found = {}
        for row in rows:
            if not _is_verified(float(row.get("rating") or 0), int(row.get("user_rating_count") or 0)):
                continue
            pid = row["provider_id"]
            if pid in found:
                found[pid]["query_hits"] = int(found[pid].get("query_hits") or 1) + 1
            else:
                found[pid] = row
        out = list(found.values())
        out.sort(
            key=lambda x: (
                float(x.get("taste_score") or 0),
                math.log10(int(x.get("user_rating_count") or 0) + 1),
                int(x.get("query_hits") or 1),
            ),
            reverse=True,
        )
        return out, {"candidate_count": meta.get("candidate_count", 0), "verified_count": len(out), "api_calls": meta.get("api_calls", 0)}
