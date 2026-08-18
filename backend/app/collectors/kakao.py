from __future__ import annotations

import asyncio
import os

import httpx

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"

BASE_TASTE_QUERY_SPECS = [
    ("전체", "{city} 맛집", "FD6"),
    ("전체", "{city} 현지인 맛집", "FD6"),
    ("전체", "{city} 오래된 맛집", "FD6"),
    ("한식", "{city} 한식 맛집", "FD6"),
    ("한식", "{city} 국밥 맛집", "FD6"),
    ("한식", "{city} 갈비 맛집", "FD6"),
    ("한식", "{city} 삼겹살 맛집", "FD6"),
    ("한식", "{city} 칼국수 맛집", "FD6"),
    ("한식", "{city} 냉면 맛집", "FD6"),
    ("한식", "{city} 백반 맛집", "FD6"),
    ("한식", "{city} 곱창 맛집", "FD6"),
    ("한식", "{city} 족발 보쌈 맛집", "FD6"),
    ("한식", "{city} 해산물 맛집", "FD6"),
    ("중식", "{city} 중식 맛집", "FD6"),
    ("중식", "{city} 중국집", "FD6"),
    ("중식", "{city} 짜장면 맛집", "FD6"),
    ("중식", "{city} 간짜장 맛집", "FD6"),
    ("중식", "{city} 짬뽕 맛집", "FD6"),
    ("중식", "{city} 탕수육 맛집", "FD6"),
    ("일식", "{city} 일식 맛집", "FD6"),
    ("일식", "{city} 스시 맛집", "FD6"),
    ("일식", "{city} 회 맛집", "FD6"),
    ("일식", "{city} 라멘 맛집", "FD6"),
    ("일식", "{city} 돈카츠 맛집", "FD6"),
    ("양식", "{city} 양식 맛집", "FD6"),
    ("양식", "{city} 파스타 맛집", "FD6"),
    ("양식", "{city} 스테이크 맛집", "FD6"),
    ("아시아", "{city} 베트남 음식 맛집", "FD6"),
    ("아시아", "{city} 태국 음식 맛집", "FD6"),
    ("분식", "{city} 분식 맛집", "FD6"),
]

# 카페는 단순 "지역+카페" 한 번으로는 대형/베이커리/뷰 카페를 많이 놓친다.
# 여러 의도 검색을 합집합으로 모으고, 추천 단계에서 반복 노출/교차 근거로 거른다.
CAFE_QUERY_SPECS = [
    ("카페", "{city} 카페", "CE7"),
    ("카페", "{city} 대형카페", "CE7"),
    ("카페", "{city} 베이커리카페", "CE7"),
    ("카페", "{city} 브런치카페", "CE7"),
    ("카페", "{city} 뷰카페", "CE7"),
    ("카페", "{city} 오션뷰카페", "CE7"),
    ("카페", "{city} 정원카페", "CE7"),
    ("카페", "{city} 야외카페", "CE7"),
    ("카페", "{city} 한옥카페", "CE7"),
    ("카페", "{city} 로스터리카페", "CE7"),
    ("카페", "{city} 디저트카페", "CE7"),
    ("카페", "{city} 데이트카페", "CE7"),
    ("카페", "{city} 감성카페", "CE7"),
    ("디저트", "{city} 디저트 맛집", None),
    ("디저트", "{city} 베이커리 맛집", None),
]

# 현재 안산을 실제 회귀 테스트 지역으로 사용 중이라, 행정시 이름 검색에서 밀리는
# 대표 생활권/관광권을 보조 질의로 더한다. 다른 지역도 검증하면서 같은 방식으로 확장한다.
REGION_SPECIAL_QUERY_SPECS = {
    "안산시": [
        ("카페", "대부도 카페", "CE7"),
        ("카페", "대부도 대형카페", "CE7"),
        ("카페", "대부도 오션뷰카페", "CE7"),
        ("카페", "대부도 베이커리카페", "CE7"),
    ]
}

TASTE_QUERY_SPECS = BASE_TASTE_QUERY_SPECS + CAFE_QUERY_SPECS


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

    def _normalize(self, d: dict, province: str, city: str, category: str | None = None, query: str | None = None):
        return {
            "provider": "kakao",
            "provider_id": str(d.get("id") or ""),
            "province": province,
            "city": city,
            "name": d.get("place_name"),
            "category": category or d.get("category_name"),
            "business_type": d.get("category_group_name") or "장소",
            "address": d.get("address_name"),
            "road_address": d.get("road_address_name"),
            "phone": d.get("phone"),
            "x": d.get("x"),
            "y": d.get("y"),
            "place_url": d.get("place_url"),
            "status": "Kakao 검색 노출",
            "verified_public": False,
            "query_category": category,
            "query_text": query,
            "query_hits": 1,
            "raw_json": d,
        }

    def _belongs_to_city(self, d: dict, city: str):
        addr = f"{d.get('address_name', '')} {d.get('road_address_name', '')}".strip()
        return (not addr) or (city in addr)

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
    def _grid_rects(bbox, n: int = 3):
        minx, miny, maxx, maxy = map(float, bbox)
        dx = (maxx - minx) / n
        dy = (maxy - miny) / n
        rects = []
        for ix in range(n):
            for iy in range(n):
                x1 = minx + dx * ix
                x2 = maxx if ix == n - 1 else minx + dx * (ix + 1)
                y1 = miny + dy * iy
                y2 = maxy if iy == n - 1 else miny + dy * (iy + 1)
                rects.append((x1, y1, x2, y2))
        return rects

    async def _get(self, client, url, params):
        self.api_calls += 1
        r = await client.get(url, headers=self._headers(), params=params)
        if r.status_code != 200:
            try:
                body = r.json()
                msg = body.get("msg") or body.get("message") or str(body)
            except Exception:
                msg = r.text[:300]
            raise RuntimeError(f"Kakao HTTP {r.status_code}: {msg}")
        return r.json()

    async def _query_once(self, client, province, city, spec):
        category, template, code = spec
        query = template.format(city=city)
        params = {"query": query, "page": 1, "size": 15, "sort": "accuracy"}
        if code:
            params["category_group_code"] = code
        data = await self._get(client, KEYWORD_URL, params)
        rows = []
        for d in data.get("documents", []):
            if self._belongs_to_city(d, city):
                rows.append(self._normalize(d, province, city, category, query))
        return rows

    async def _category_rect_once(self, client, province, city, category, code, rect):
        rect_text = ",".join(f"{v:.8f}" for v in rect)
        params = {
            "category_group_code": code,
            "rect": rect_text,
            "page": 1,
            "size": 15,
            "sort": "accuracy",
        }
        data = await self._get(client, CATEGORY_URL, params)
        query = f"{city} {category} 공간후보"
        rows = []
        for d in data.get("documents", []):
            if self._belongs_to_city(d, city):
                rows.append(self._normalize(d, province, city, category, query))
        return rows

    def _specs_for_city(self, city: str):
        return TASTE_QUERY_SPECS + REGION_SPECIAL_QUERY_SPECS.get(city, [])

    async def collect_taste_candidates(self, province: str, city: str, bbox=None):
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0}

        self.api_calls = 0
        self.last_mode = "taste_queries_plus_cafe_grid"
        timeout = httpx.Timeout(10.0, connect=4.0)
        sem = asyncio.Semaphore(10)
        specs = self._specs_for_city(city)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async def run_keyword(spec):
                async with sem:
                    return await self._query_once(client, province, city, spec)

            tasks = [run_keyword(spec) for spec in specs]

            # bbox가 있으면 카페만 3x3 공간 샘플링을 병렬 추가한다.
            # 과거 수백 회 재귀 크롤 대신 9회로 제한해 속도와 Recall을 절충한다.
            spatial_calls = 0
            if self._valid_bbox(bbox):
                rects = self._grid_rects(bbox, n=3)
                spatial_calls = len(rects)

                async def run_rect(rect):
                    async with sem:
                        return await self._category_rect_once(client, province, city, "카페", "CE7", rect)

                tasks.extend(run_rect(rect) for rect in rects)

            batches = await asyncio.gather(*tasks)

        rows = [r for batch in batches for r in batch]
        return rows, {
            "candidate_count": len(rows),
            "api_calls": self.api_calls,
            "keyword_queries": len(specs),
            "cafe_spatial_calls": spatial_calls,
        }

    async def collect_places(self, province: str, city: str, bbox=None):
        rows, _ = await self.collect_taste_candidates(province, city, bbox)
        found = {}
        for row in rows:
            found[row["provider_id"]] = row
        return list(found.values())

    async def search_restaurants(self, province: str, city: str, pages: int = 3):
        return await self.collect_places(province, city, bbox=None)
