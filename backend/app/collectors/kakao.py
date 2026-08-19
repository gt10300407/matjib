from __future__ import annotations

import asyncio
import os

import httpx

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}

# Popularity/taste-intent discovery only. No menu-by-menu crawling and no city/store hardcoding.
POPULARITY_QUERY_SPECS = [
    ("전체", "{city} 맛집", "FD6"),
    ("전체", "{city} 현지인 맛집", "FD6"),
    ("전체", "{city} 유명 맛집", "FD6"),
    ("전체", "{city} 오래된 맛집", "FD6"),
    ("카페", "{city} 인기 카페", "CE7"),
    ("카페", "{city} 유명 카페", "CE7"),
]

TASTE_QUERY_SPECS = POPULARITY_QUERY_SPECS


def _infer_cuisine(category_name: str | None) -> str:
    text = category_name or ""
    if "카페" in text or "커피" in text:
        return "카페"
    if "제과" in text or "베이커리" in text or "디저트" in text:
        return "디저트"
    if "중식" in text or "중국" in text:
        return "중식"
    if "일식" in text or "일본" in text:
        return "일식"
    if any(x in text for x in ("양식", "이탈리안", "프랑스", "스테이크", "피자")):
        return "양식"
    if any(x in text for x in ("베트남", "태국", "인도", "아시아")):
        return "아시아"
    if "분식" in text:
        return "분식"
    if "한식" in text:
        return "한식"
    return "기타"


def _is_food_place(d: dict) -> bool:
    group = str(d.get("category_group_code") or "")
    category = str(d.get("category_name") or "")
    return group in {"FD6", "CE7"} or "음식점" in category or "카페" in category or "제과" in category


def _is_mass_market_fast_food(d: dict) -> bool:
    """Generic taxonomy filter for recommendation discovery.

    This deliberately does not contain franchise/store names. Direct user search still
    returns these places; they are only excluded from automatic 'local 맛집' ranking.
    """
    category = str(d.get("category_name") or "")
    return "패스트푸드" in category


class KakaoCollector:
    def __init__(self):
        self.key = os.getenv("KAKAO_REST_API_KEY", "").strip()
        self.api_calls = 0
        self.last_mode = "none"

    @property
    def enabled(self):
        return bool(self.key)

    def _headers(self):
        return {"Authorization": f"KakaoAK {self.key}"}

    @staticmethod
    def _belongs_to_city(d: dict, city: str) -> bool:
        addr = f"{d.get('address_name', '')} {d.get('road_address_name', '')}".strip()
        return (not addr) or city in addr

    def _normalize(self, d: dict, province: str, city: str, query: str, query_category: str, mode: str):
        cuisine = _infer_cuisine(d.get("category_name"))
        return {
            "provider": "kakao",
            "provider_id": str(d.get("id") or ""),
            "province": province,
            "city": city,
            "name": d.get("place_name"),
            "category": cuisine,
            "cuisine": cuisine,
            "business_type": d.get("category_group_name") or "장소",
            "address": d.get("address_name"),
            "road_address": d.get("road_address_name"),
            "phone": d.get("phone"),
            "x": d.get("x"),
            "y": d.get("y"),
            "place_url": d.get("place_url"),
            "status": "Kakao 검색 노출",
            "verified_public": False,
            "query_category": query_category,
            "query_text": query,
            "query_hits": 1,
            "discovery_mode": mode,
            "raw_json": d,
        }

    async def _get(self, client: httpx.AsyncClient, params: dict):
        last = None
        for attempt in range(3):
            self.api_calls += 1
            last = await client.get(KEYWORD_URL, headers=self._headers(), params=params)
            if last.status_code not in _TRANSIENT_HTTP:
                break
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
        if last is None:
            raise RuntimeError("Kakao 응답 없음")
        if last.status_code != 200:
            try:
                body = last.json()
                msg = body.get("msg") or body.get("message") or str(body)
            except Exception:
                msg = last.text[:300]
            raise RuntimeError(f"Kakao HTTP {last.status_code}: {msg}")
        return last.json()

    async def _query_once(self, client: httpx.AsyncClient, province: str, city: str, spec):
        category, template, code = spec
        query = template.format(city=city)
        params = {"query": query, "page": 1, "size": 15, "sort": "accuracy"}
        if code:
            params["category_group_code"] = code
        data = await self._get(client, params)
        rows = []
        for d in data.get("documents", []):
            if _is_food_place(d) and not _is_mass_market_fast_food(d) and self._belongs_to_city(d, city):
                # evidence.py intentionally recognizes `keyword` as explicit taste-intent evidence.
                rows.append(self._normalize(d, province, city, query, category, "keyword"))
        return rows

    async def search_direct(self, province: str, city: str, query: str):
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0}
        q = " ".join(x for x in (city.strip(), query.strip()) if x)
        if not q:
            return [], {"candidate_count": 0, "api_calls": 0}
        before = self.api_calls
        timeout = httpx.Timeout(9.0, connect=3.5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            data = await self._get(client, {"query": q, "page": 1, "size": 15, "sort": "accuracy"})
        rows = [
            self._normalize(d, province, city, q, "전체", "direct_search")
            for d in data.get("documents", [])
            if _is_food_place(d) and self._belongs_to_city(d, city)
        ]
        return rows, {"candidate_count": len(rows), "api_calls": self.api_calls - before, "query": q}

    async def collect_taste_candidates(self, province: str, city: str, bbox=None):
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0}

        self.api_calls = 0
        self.last_mode = "taste_intent_keyword_discovery"
        timeout = httpx.Timeout(10.0, connect=3.5)
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async def run(spec):
                async with sem:
                    return await self._query_once(client, province, city, spec)

            batches = await asyncio.gather(*(run(spec) for spec in POPULARITY_QUERY_SPECS))

        rows = [r for batch in batches for r in batch]
        return rows, {
            "candidate_count": len(rows),
            "api_calls": self.api_calls,
            "keyword_queries": len(POPULARITY_QUERY_SPECS),
            "spatial_cells": 0,
            "discovery_definition": "6 explicit taste/popularity queries; no exhaustive spatial inventory",
        }

    async def collect_places(self, province: str, city: str, bbox=None):
        rows, _ = await self.collect_taste_candidates(province, city, bbox)
        found = {}
        for row in rows:
            pid = row.get("provider_id")
            if not pid:
                continue
            if pid not in found:
                found[pid] = row
                found[pid]["query_hits"] = 1
            else:
                found[pid]["query_hits"] = int(found[pid].get("query_hits") or 1) + 1
        return list(found.values())

    async def search_restaurants(self, province: str, city: str, pages: int = 3):
        return await self.collect_places(province, city)
