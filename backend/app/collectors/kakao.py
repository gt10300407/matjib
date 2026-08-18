from __future__ import annotations

import math
import os
import httpx

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"

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

    def _normalize(self, d: dict, province: str, city: str):
        return {
            "provider": "kakao",
            "provider_id": str(d.get("id") or ""),
            "province": province,
            "city": city,
            "name": d.get("place_name"),
            "category": d.get("category_name"),
            "business_type": d.get("category_group_name") or "장소",
            "address": d.get("address_name"),
            "road_address": d.get("road_address_name"),
            "phone": d.get("phone"),
            "x": d.get("x"),
            "y": d.get("y"),
            "place_url": d.get("place_url"),
            "status": "검색 노출",
            "verified_public": False,
            "raw_json": d,
        }

    def _belongs_to_city(self, d: dict, city: str):
        addr = f"{d.get('address_name','')} {d.get('road_address_name','')}".strip()
        return (not addr) or (city in addr)

    async def _get(self, client, url, params):
        self.api_calls += 1
        r = await client.get(url, headers=self._headers(), params=params)
        if r.status_code != 200:
            try:
                body = r.json()
                msg = body.get("msg") or body.get("message") or str(body)
            except Exception:
                msg = r.text[:300]
            if r.status_code == 403:
                raise RuntimeError(f"Kakao 403: Kakao Map API 사용 설정/권한 확인 필요. {msg}")
            raise RuntimeError(f"Kakao HTTP {r.status_code}: {msg}")
        return r.json()

    @staticmethod
    def _valid_bbox(bbox):
        if not bbox or len(bbox) != 4:
            return False
        try:
            minx, miny, maxx, maxy = map(float, bbox)
        except Exception:
            return False
        return -180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90

    @staticmethod
    def _split_bbox(bbox):
        minx, miny, maxx, maxy = map(float, bbox)
        mx = (minx + maxx) / 2
        my = (miny + maxy) / 2
        return [[minx, miny, mx, my],[mx, miny, maxx, my],[minx, my, mx, maxy],[mx, my, maxx, maxy]]

    async def _collect_category_rect(self, client, province, city, category_code, bbox, *, max_depth=6, max_calls=220, max_results=2500):
        found = {}
        start_calls = self.api_calls

        async def walk(rect, depth):
            if len(found) >= max_results or (self.api_calls - start_calls) >= max_calls:
                return
            rect_str = ",".join(f"{float(v):.8f}" for v in rect)
            params = {"category_group_code": category_code,"rect": rect_str,"page": 1,"size": 15,"sort": "accuracy"}
            first = await self._get(client, CATEGORY_URL, params)
            meta = first.get("meta", {})
            total_count = int(meta.get("total_count") or 0)
            pageable_count = int(meta.get("pageable_count") or 0)
            width = float(rect[2]) - float(rect[0])
            height = float(rect[3]) - float(rect[1])
            can_split = depth < max_depth and width > 0.001 and height > 0.001

            if total_count > 45 and can_split:
                for child in self._split_bbox(rect):
                    await walk(child, depth + 1)
                    if len(found) >= max_results or (self.api_calls - start_calls) >= max_calls:
                        break
                return

            docs = list(first.get("documents", []))
            pages = min(3, max(1, math.ceil(max(pageable_count, len(docs)) / 15)))
            for page in range(2, pages + 1):
                if (self.api_calls - start_calls) >= max_calls:
                    break
                page_params = dict(params)
                page_params["page"] = page
                data = await self._get(client, CATEGORY_URL, page_params)
                docs.extend(data.get("documents", []))
                if data.get("meta", {}).get("is_end", False):
                    break

            for d in docs:
                pid = str(d.get("id") or "")
                if pid and self._belongs_to_city(d, city):
                    found[pid] = self._normalize(d, province, city)

        await walk(list(map(float, bbox)), 0)
        return list(found.values())

    async def _keyword_pages(self, client, province, city, query, category_code=None):
        out = {}
        for page in range(1, 4):
            params = {"query": query, "page": page, "size": 15, "sort": "accuracy"}
            if category_code:
                params["category_group_code"] = category_code
            data = await self._get(client, KEYWORD_URL, params)
            for d in data.get("documents", []):
                pid = str(d.get("id") or "")
                if pid and self._belongs_to_city(d, city):
                    out[pid] = self._normalize(d, province, city)
            if data.get("meta", {}).get("is_end", True):
                break
        return list(out.values())

    async def _collect_keyword_fallback(self, client, province, city):
        queries = [
            (f"{city} 맛집", "FD6"),(f"{city} 한식", "FD6"),(f"{city} 고기", "FD6"),(f"{city} 해산물", "FD6"),(f"{city} 일식", "FD6"),(f"{city} 중식", "FD6"),(f"{city} 분식", "FD6"),(f"{city} 카페", "CE7"),(f"{city} 베이커리", None),
        ]
        found = {}
        for query, code in queries:
            rows = await self._keyword_pages(client, province, city, query, code)
            for row in rows:
                found[row["provider_id"]] = row
        return list(found.values())

    async def collect_places(self, province: str, city: str, bbox=None):
        if not self.enabled:
            return []
        self.api_calls = 0
        timeout = httpx.Timeout(20.0, connect=8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if self._valid_bbox(bbox):
                self.last_mode = "spatial"
                restaurants = await self._collect_category_rect(client, province, city, "FD6", bbox,max_calls=230, max_results=3000)
                cafes = await self._collect_category_rect(client, province, city, "CE7", bbox,max_calls=150, max_results=1500)
                found = {r["provider_id"]: r for r in restaurants}
                for r in cafes:
                    found[r["provider_id"]] = r
                return list(found.values())
            self.last_mode = "keyword_fallback"
            return await self._collect_keyword_fallback(client, province, city)

    async def search_restaurants(self, province: str, city: str, pages: int = 3):
        return await self.collect_places(province, city, bbox=None)
