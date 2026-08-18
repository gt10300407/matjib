from __future__ import annotations

import asyncio
import hashlib
import json
import os

import httpx

from ..diagnostics import _extract_public_error, normalize_public_key

GENERAL_URL = "https://apis.data.go.kr/1741000/general_restaurants/info"
REST_CAFE_URL = "https://apis.data.go.kr/1741000/rest_cafes/info"
BAKERY_URL = "https://apis.data.go.kr/1741000/bakeries/info"
EXCELLENT_URL = "https://apis.data.go.kr/1741000/excellent_restaurant_info/info"

_RECORD_KEYS = {"BPLC_NM", "BSNSSP_NM", "ROAD_NM_ADDR", "SITE_WHL_ADDR", "PRINC_FD_KND"}
_TOTAL_KEYS = {"totalCount", "total_count", "totCnt", "totalCnt", "matchCount"}
_CLOSED_WORDS = ("폐업", "취소", "말소", "종료", "직권말소")

LICENSE_SOURCES = (
    ("general", "일반음식점", GENERAL_URL),
    ("rest_cafe", "휴게음식점", REST_CAFE_URL),
    ("bakery", "제과점", BAKERY_URL),
)


def records(node):
    found = []

    def walk(x):
        if isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            if set(x) & _RECORD_KEYS:
                found.append(x)
            else:
                for v in x.values():
                    walk(v)

    walk(node)
    return found


def total_count(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _TOTAL_KEYS:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
        for value in node.values():
            found = total_count(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = total_count(value)
            if found is not None:
                return found
    return None


def pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def pid(provider, d):
    raw = pick(d, "MNG_NO", "BPLC_NM", "BSNSSP_NM", "ROAD_NM_ADDR") or json.dumps(
        d, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha1((provider + "|" + raw).encode()).hexdigest()


def _is_active(d: dict) -> bool:
    status = " ".join(
        filter(
            None,
            [
                pick(d, "SALS_STTS_NM", "DTL_SALS_STTS_NM", "BSN_STATE_NM"),
                pick(d, "DTL_SALS_STTS_CD", "SALS_STTS_CD"),
            ],
        )
    )
    return not any(word in status for word in _CLOSED_WORDS)


def _belongs_to_city(d: dict, city: str) -> bool:
    address = " ".join(
        filter(
            None,
            [
                pick(d, "ROAD_NM_ADDR", "RDNMADR"),
                pick(d, "SITE_WHL_ADDR", "SITE_ADDR"),
            ],
        )
    )
    return (not address) or city in address


def normalize_license_row(d: dict, province: str, city: str, provider: str, license_type: str) -> dict:
    category = pick(d, "UPTAE_NM", "PRINC_FD_KND", "FD_KND", "BSNSSP_NM") or license_type
    return {
        "provider": provider,
        "provider_id": pid(provider, d),
        "province": province,
        "city": city,
        "name": pick(d, "BPLC_NM", "BIZPLC_NM", "BSNSSP_NM") or "이름없음",
        "category": category,
        "cuisine": category,
        "business_type": f"{license_type} 인허가",
        "address": pick(d, "SITE_WHL_ADDR", "SITE_ADDR"),
        "road_address": pick(d, "ROAD_NM_ADDR", "RDNMADR"),
        "phone": pick(d, "TELNO", "TEL", "PHONE"),
        # These LOCALDATA license datasets use EPSG:5174. Do not treat them as WGS84.
        "x": None,
        "y": None,
        "place_url": None,
        "status": pick(d, "SALS_STTS_NM", "DTL_SALS_STTS_NM", "BSN_STATE_NM") or "상태 미표기",
        "verified_public": False,
        "query_category": f"공공 {license_type} 명부",
        "query_text": f"{city} {license_type} 인허가 명부",
        "query_hits": 1,
        "discovery_mode": "official_inventory",
        "raw_json": d,
    }


def normalize_general_row(d: dict, province: str, city: str) -> dict:
    return normalize_license_row(d, province, city, "general", "일반음식점")


class PublicDataCollector:
    def __init__(self):
        self.key = normalize_public_key(os.getenv("DATA_GO_KR_SERVICE_KEY", ""))
        self.api_calls = 0

    @property
    def enabled(self):
        return bool(self.key)

    async def _page(self, client: httpx.AsyncClient, url: str, params: dict, page_no: int, page_size: int):
        if not self.enabled:
            return [], None
        payload_params = {
            "serviceKey": self.key,
            "pageNo": page_no,
            "numOfRows": page_size,
            "returnType": "json",
            **params,
        }
        self.api_calls += 1
        r = await client.get(url, params=payload_params)
        text = r.text[:12000]
        code, msg = _extract_public_error(text)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {msg or text[:300]}")
        if code:
            raise RuntimeError(f"{code}: {msg}")
        try:
            payload = r.json()
        except Exception as e:
            raise RuntimeError(f"공공데이터 API 비JSON 응답: {text[:300]}") from e
        return records(payload), total_count(payload)

    async def call(self, url, params, page_no: int = 1, page_size: int = 100):
        before = self.api_calls
        timeout = httpx.Timeout(20, connect=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            rows, _ = await self._page(client, url, params, page_no, page_size)
        return rows, self.api_calls - before

    async def _inventory_one(
        self,
        province: str,
        city: str,
        provider: str,
        license_type: str,
        url: str,
        *,
        page_size: int = 500,
        max_pages: int = 40,
    ):
        if not self.enabled:
            return [], {"ok": False, "candidate_count": 0, "api_calls": 0, "pages": 0, "reason": "no_key"}

        before = self.api_calls
        params = {"cond[ROAD_NM_ADDR::LIKE]": city}
        timeout = httpx.Timeout(25, connect=5)
        out = []
        total = None
        pages = 0

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for page in range(1, max_pages + 1):
                batch, page_total = await self._page(client, url, params, page, page_size)
                pages = page
                if total is None and page_total is not None:
                    total = page_total
                for d in batch:
                    if _belongs_to_city(d, city) and _is_active(d):
                        out.append(normalize_license_row(d, province, city, provider, license_type))
                if not batch:
                    break
                if total is not None and page * page_size >= total:
                    break
                if len(batch) < page_size:
                    break

        dedup = {row["provider_id"]: row for row in out}
        rows = list(dedup.values())
        truncated = bool(total is not None and pages * page_size < total and pages >= max_pages)
        return rows, {
            "ok": True,
            "candidate_count": len(rows),
            "api_calls": self.api_calls - before,
            "pages": pages,
            "source_total": total,
            "page_size": page_size,
            "truncated": truncated,
            "license_type": license_type,
        }

    async def licensed_inventory(self, province: str, city: str):
        """Build a factual city master from independently-authorized license APIs.

        A user's public-data service key may be authorized for only some services.
        Each dataset therefore fails independently; one authorization error must not
        discard the other masters.
        """
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0, "sources": {}, "partial": False}

        async def one(provider, license_type, url):
            try:
                return provider, await self._inventory_one(province, city, provider, license_type, url)
            except Exception as exc:
                return provider, ([], {
                    "ok": False,
                    "candidate_count": 0,
                    "api_calls": 0,
                    "pages": 0,
                    "license_type": license_type,
                    "error": {"type": type(exc).__name__, "message": str(exc) or type(exc).__name__},
                })

        before = self.api_calls
        results = await asyncio.gather(*(one(*source) for source in LICENSE_SOURCES))
        rows = []
        source_meta = {}
        for provider, (batch, meta) in results:
            rows.extend(batch)
            source_meta[provider] = meta

        dedup = {row["provider_id"]: row for row in rows}
        out = list(dedup.values())
        ok_count = sum(1 for meta in source_meta.values() if meta.get("ok"))
        return out, {
            "candidate_count": len(out),
            "api_calls": self.api_calls - before,
            "sources": source_meta,
            "partial": 0 < ok_count < len(source_meta),
            "available_sources": [k for k, v in source_meta.items() if v.get("ok")],
            "unavailable_sources": [k for k, v in source_meta.items() if not v.get("ok")],
            "discovery_definition": "city-wide general + rest-cafe + bakery licensing master",
        }

    async def general_inventory(self, province: str, city: str, *, page_size: int = 500, max_pages: int = 40):
        rows, meta = await self._inventory_one(
            province, city, "general", "일반음식점", GENERAL_URL, page_size=page_size, max_pages=max_pages
        )
        return rows, {**meta, "discovery_definition": "city-wide general restaurant licensing master"}

    async def excellent(self, province, city):
        rows, _ = await self.call(EXCELLENT_URL, {"cond[ROAD_NM_ADDR::LIKE]": city}, page_size=500)
        out = []
        for d in rows:
            if not _belongs_to_city(d, city):
                continue
            out.append(
                {
                    "provider": "excellent",
                    "provider_id": pid("excellent", d),
                    "province": province,
                    "city": city,
                    "name": pick(d, "BPLC_NM", "BSNSSP_NM", "BIZPLC_NM") or "이름없음",
                    "category": pick(d, "PRINC_FD_KND", "UPTAE_NM", "FD_KND"),
                    "business_type": "모범음식점",
                    "address": pick(d, "SITE_WHL_ADDR", "SITE_ADDR"),
                    "road_address": pick(d, "ROAD_NM_ADDR", "RDNMADR"),
                    "phone": pick(d, "TELNO", "TEL", "PHONE"),
                    "x": None,
                    "y": None,
                    "place_url": None,
                    "status": pick(d, "SALS_STTS_NM", "DTL_SALS_STTS_NM", "DSGN_STTS_NM") or "지정",
                    "verified_public": True,
                    "query_category": "공식정보",
                    "query_text": f"{city} 모범음식점",
                    "query_hits": 1,
                    "discovery_mode": "official",
                    "raw_json": d,
                }
            )
        return out

    async def verify_by_name(self, city, name):
        """Search every available license dataset by business name.

        This is a low-cost fallback used only when the city master is not cached.
        Individual service authorization failures are ignored and reported through
        the returned normalized rows only from services that actually responded.
        """
        out = []
        for provider, license_type, url in LICENSE_SOURCES:
            try:
                rows, _ = await self.call(
                    url,
                    {"cond[BPLC_NM::LIKE]": name, "cond[ROAD_NM_ADDR::LIKE]": city},
                    page_size=100,
                )
            except Exception:
                continue
            for d in rows:
                if _belongs_to_city(d, city) and _is_active(d):
                    out.append(normalize_license_row(d, "", city, provider, license_type))
        return out
