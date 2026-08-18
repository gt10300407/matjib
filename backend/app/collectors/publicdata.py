from __future__ import annotations

import hashlib
import json
import os

import httpx

from ..diagnostics import _extract_public_error, normalize_public_key

GENERAL_URL = "https://apis.data.go.kr/1741000/general_restaurants/info"
EXCELLENT_URL = "https://apis.data.go.kr/1741000/excellent_restaurant_info/info"

_RECORD_KEYS = {"BPLC_NM", "BSNSSP_NM", "ROAD_NM_ADDR", "SITE_WHL_ADDR", "PRINC_FD_KND"}
_TOTAL_KEYS = {"totalCount", "total_count", "totCnt", "totalCnt", "matchCount"}
_CLOSED_WORDS = ("폐업", "취소", "말소", "종료", "직권말소")


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
    """Best-effort total count extraction across public-data response wrappers."""
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
    """Exclude only rows that explicitly say they are closed/cancelled.

    LOCALDATA status-code dictionaries can evolve, so this deliberately avoids
    hardcoding one numeric 'active' code. Unknown/missing status stays in the
    master and can be cross-checked by other sources.
    """
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


def normalize_general_row(d: dict, province: str, city: str) -> dict:
    category = pick(d, "UPTAE_NM", "PRINC_FD_KND", "FD_KND", "BSNSSP_NM") or "일반음식점"
    return {
        "provider": "general",
        "provider_id": pid("general", d),
        "province": province,
        "city": city,
        "name": pick(d, "BPLC_NM", "BIZPLC_NM", "BSNSSP_NM") or "이름없음",
        "category": category,
        "cuisine": category,
        "business_type": "일반음식점 인허가",
        "address": pick(d, "SITE_WHL_ADDR", "SITE_ADDR"),
        "road_address": pick(d, "ROAD_NM_ADDR", "RDNMADR"),
        "phone": pick(d, "TELNO", "TEL", "PHONE"),
        # LOCALDATA general-restaurant coordinates use EPSG:5174, not WGS84.
        # Do not feed them into geographic matching until transformed explicitly.
        "x": None,
        "y": None,
        "place_url": None,
        "status": pick(d, "SALS_STTS_NM", "DTL_SALS_STTS_NM", "BSN_STATE_NM") or "상태 미표기",
        "verified_public": False,
        "query_category": "공공 음식점 명부",
        "query_text": f"{city} 일반음식점 인허가 명부",
        "query_hits": 1,
        "discovery_mode": "official_inventory",
        "raw_json": d,
    }


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
        self.api_calls = 0
        timeout = httpx.Timeout(20, connect=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            rows, _ = await self._page(client, url, params, page_no, page_size)
        return rows

    async def general_inventory(
        self,
        province: str,
        city: str,
        *,
        page_size: int = 500,
        max_pages: int = 40,
    ):
        """Fetch the city's general-restaurant licensing master.

        The source is free, daily-updated public data. Results are safe to cache in
        our own DB; unlike TMAP this dataset has no 24-hour storage restriction.
        Pagination stops from response total-count when present, otherwise when a
        short page is returned. `max_pages` protects the server from runaway calls.
        """
        if not self.enabled:
            return [], {"candidate_count": 0, "api_calls": 0, "pages": 0, "truncated": False}

        self.api_calls = 0
        params = {"cond[ROAD_NM_ADDR::LIKE]": city}
        timeout = httpx.Timeout(25, connect=5)
        out = []
        total = None
        pages = 0

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for page in range(1, max_pages + 1):
                batch, page_total = await self._page(client, GENERAL_URL, params, page, page_size)
                pages = page
                if total is None and page_total is not None:
                    total = page_total
                for d in batch:
                    if _belongs_to_city(d, city) and _is_active(d):
                        out.append(normalize_general_row(d, province, city))

                if not batch:
                    break
                if total is not None and page * page_size >= total:
                    break
                if len(batch) < page_size:
                    break

        dedup = {}
        for row in out:
            dedup[row["provider_id"]] = row
        rows = list(dedup.values())
        truncated = bool(total is not None and pages * page_size < total and pages >= max_pages)
        return rows, {
            "candidate_count": len(rows),
            "api_calls": self.api_calls,
            "pages": pages,
            "source_total": total,
            "page_size": page_size,
            "truncated": truncated,
            "discovery_definition": "city-wide general restaurant licensing master",
        }

    async def excellent(self, province, city):
        rows = await self.call(EXCELLENT_URL, {"cond[ROAD_NM_ADDR::LIKE]": city}, page_size=500)
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
        return await self.call(
            GENERAL_URL,
            {
                "cond[BPLC_NM::LIKE]": name,
                "cond[ROAD_NM_ADDR::LIKE]": city,
            },
            page_size=100,
        )
