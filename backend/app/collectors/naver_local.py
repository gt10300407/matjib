from __future__ import annotations

import html
import os
import re

import httpx

NAVER_LOCAL_URL = "https://openapi.naver.com/v1/search/local.json"


def _clean_title(value: str | None) -> str:
    text = html.unescape(value or "")
    return re.sub(r"<[^>]+>", "", text).strip()


class NaverLocalCollector:
    """Optional discovery source.

    Naver local search is used only to broaden discovery/evidence. A place is never
    recommended merely because it appears in Naver results.
    """

    def __init__(self):
        self.client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
        self.api_calls = 0

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def search(self, province: str, city: str, specs: list[tuple[str, str]], *, max_queries: int = 18):
        if not self.enabled:
            return []

        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        rows = []
        timeout = httpx.Timeout(10.0, connect=4.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for category, query in specs[:max_queries]:
                self.api_calls += 1
                r = await client.get(
                    NAVER_LOCAL_URL,
                    headers=headers,
                    params={"query": query, "display": 5, "sort": "random"},
                )
                if r.status_code != 200:
                    raise RuntimeError(f"Naver Local HTTP {r.status_code}: {r.text[:250]}")
                for item in r.json().get("items", []):
                    address = item.get("roadAddress") or item.get("address") or ""
                    if address and city not in address:
                        continue
                    name = _clean_title(item.get("title"))
                    if not name:
                        continue
                    rows.append({
                        "provider": "naver",
                        "provider_id": f"naver:{item.get('mapx','')}:{item.get('mapy','')}:{name}",
                        "province": province,
                        "city": city,
                        "name": name,
                        "category": item.get("category") or category,
                        "business_type": item.get("category") or "지역검색",
                        "address": item.get("address"),
                        "road_address": item.get("roadAddress"),
                        "phone": item.get("telephone"),
                        "x": item.get("mapx"),
                        "y": item.get("mapy"),
                        "place_url": item.get("link") or None,
                        "status": "네이버 지역검색 노출",
                        "verified_public": False,
                        "query_category": category,
                        "query_text": query,
                        "query_hits": 1,
                        "raw_json": item,
                    })
        return rows
