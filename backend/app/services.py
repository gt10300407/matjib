from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone

from .collectors.google_places import GooglePlacesCollector
from .collectors.kakao import KakaoCollector
from .evidence import entity_match_score, merge_and_rank, normalize_name, same_place
from .taste_store import TasteStore

TOP_RECOMMENDATIONS = 10


def _err(exc: Exception):
    return {"ok": False, "type": type(exc).__name__, "message": str(exc) or type(exc).__name__}


def _public_row(row):
    return {k: v for k, v in row.items() if k != "raw_json" and not k.startswith("_")}


def _phone_key(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _address_text(row: dict) -> str:
    return str(row.get("road_address") or row.get("address") or "")


def _coords_in_bbox(row: dict, bbox) -> bool:
    if not bbox or len(bbox) != 4:
        return False
    try:
        x = float(row.get("x"))
        y = float(row.get("y"))
        minx, miny, maxx, maxy = map(float, bbox)
    except (TypeError, ValueError):
        return False
    return minx <= x <= maxx and miny <= y <= maxy


def _belongs_to_requested_region(row: dict, city: str, bbox=None) -> bool:
    address = _address_text(row)
    if address:
        return city in address
    return _coords_in_bbox(row, bbox)


def _query_matches_name(query: str, name: str | None) -> bool:
    q, n = normalize_name(query), normalize_name(name)
    if not q or not n:
        return False
    if q in n or n in q:
        return True
    terms = [normalize_name(x) for x in re.split(r"\s+", query.strip()) if normalize_name(x)]
    return bool(terms) and all(t in n for t in terms)


def _attach_public_inventory(discovered: list[dict], inventory: list[dict]) -> list[dict]:
    """Backward-compatible helper retained for tests/tools.

    v4.7 normal region refresh intentionally does not crawl a city-wide public master.
    """
    by_name: dict[str, list[dict]] = {}
    by_phone: dict[str, list[dict]] = {}
    for row in inventory:
        nk = normalize_name(row.get("name"))
        if nk:
            by_name.setdefault(nk, []).append(row)
        pk = _phone_key(row.get("phone"))
        if len(pk) >= 8:
            by_phone.setdefault(pk, []).append(row)

    attached, seen = [], set()
    for row in discovered:
        candidates = []
        nk = normalize_name(row.get("name"))
        if nk:
            candidates.extend(by_name.get(nk, []))
        pk = _phone_key(row.get("phone"))
        if len(pk) >= 8:
            candidates.extend(by_phone.get(pk, []))
        for inv in candidates:
            iid = inv.get("provider_id")
            if not iid or iid in seen:
                continue
            if entity_match_score(row, inv) >= 55:
                attached.append(inv)
                seen.add(iid)
    return discovered + attached


def _best_entity(rows: list[dict], query: str, city: str, bbox=None) -> dict | None:
    candidates = [r for r in rows if _belongs_to_requested_region(r, city, bbox)]
    if not candidates:
        return None

    def score(row):
        s = 0.0
        if _query_matches_name(query, row.get("name")):
            s += 50
        if row.get("provider") == "kakao":
            s += 18
        if _address_text(row):
            s += 8
        if _phone_key(row.get("phone")):
            s += 5
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    if not ranked or score(ranked[0]) < 50:
        return None
    if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]) and not same_place(ranked[0], ranked[1]):
        return None
    return ranked[0]


def _popularity_sort_key(row: dict):
    """Popularity first, not cuisine first.

    Google rating/review volume is the strongest quantified signal currently available.
    Repeated Kakao/Google discovery and source diversity break ties. Social/blog signals
    are deliberately not fabricated until a legal/usable source is connected.
    """
    rating = float(row.get("rating") or 0)
    reviews = int(row.get("user_rating_count") or 0)
    query_hits = int(row.get("query_hits") or 0)
    source_count = int(row.get("source_count") or 0)
    evidence = row.get("evidence") or {}
    keyword_hits = sum(int(v or 0) for v in (evidence.get("keyword_source_hits") or {}).values())
    review_mass = math.log10(reviews + 1) if reviews > 0 else 0.0
    google_popularity = rating * review_mass if reviews > 0 else 0.0
    return (
        1 if reviews > 0 else 0,
        google_popularity,
        review_mass,
        keyword_hits,
        source_count,
        query_hits,
        float(row.get("taste_score") or 0),
    )


class RefreshService:
    """Popularity-first recommendation service.

    Normal region refresh no longer tries to enumerate every restaurant/cafe or crawl
    the city-wide licensing master. It discovers a compact set of already-popular
    candidates, resolves duplicates, validates the region, and stores only TOP 10.
    """

    def __init__(self, db):
        self.db = db
        self.memory_cache = {}
        self.taste_store = TasteStore(db.path)

    def cache_key(self, province, city):
        return province.strip(), city.strip()

    def get_cached_restaurants(self, province, city, limit=300):
        hard_limit = min(int(limit or TOP_RECOMMENDATIONS), TOP_RECOMMENDATIONS)
        item = self.memory_cache.get(self.cache_key(province, city))
        if item:
            return list(item.get("restaurants", []))[:hard_limit]
        try:
            return self.taste_store.get_region(province, city, hard_limit)
        except Exception:
            return []

    def get_cached_meta(self, province, city):
        return self.memory_cache.get(self.cache_key(province, city))

    def count_verified(self, province=None, city=None):
        if province and city:
            return len(self.get_cached_restaurants(province, city, TOP_RECOMMENDATIONS))
        try:
            return self.taste_store.count(province, city)
        except Exception:
            unique = {}
            for (p, c), item in self.memory_cache.items():
                if province and p != province:
                    continue
                if city and c != city:
                    continue
                for r in item.get("restaurants", []):
                    unique[r.get("provider_id")] = r
            return len(unique)

    def memory_stats(self, province=None, city=None):
        return self.count_verified(province, city)

    def search_verified(self, q, limit=100):
        try:
            return self.taste_store.search(q, limit)
        except Exception:
            ql, rows = q.lower(), []
            for item in self.memory_cache.values():
                for r in item.get("restaurants", []):
                    hay = " ".join(str(r.get(k) or "") for k in ("name", "city", "cuisine", "address")).lower()
                    if ql in hay:
                        rows.append(r)
            rows.sort(key=_popularity_sort_key, reverse=True)
            return rows[:limit]

    async def _safe(self, name, coro):
        try:
            rows, meta = await coro
            return rows, {"ok": True, "source": name, "count": len(rows), "meta": meta, "error": None}
        except Exception as exc:
            return [], {"ok": False, "source": name, "count": 0, "meta": {}, "error": _err(exc)}

    async def live_search(self, province: str, city: str, q: str, bbox=None):
        query = q.strip()
        if not query:
            return {"ok": False, "message": "검색어가 비어 있어.", "restaurants": [], "candidate_count": 0}

        google, kakao = GooglePlacesCollector(), KakaoCollector()
        tasks, names = [], []
        if kakao.enabled:
            names.append("kakao")
            tasks.append(self._safe("kakao", kakao.search_direct(province, city, query)))
        if google.enabled:
            names.append("google")
            tasks.append(self._safe("google", google.search_direct(province, city, query, bbox)))

        if not tasks:
            return {"ok": False, "message": "검색 가능한 장소 API가 없어.", "restaurants": [], "candidate_count": 0}

        results = await asyncio.gather(*tasks)
        source_results, candidates = {}, []
        for name, (rows, status) in zip(names, results):
            source_results[name] = status
            candidates.extend(r for r in rows if _belongs_to_requested_region(r, city, bbox))

        recommendations = merge_and_rank(candidates, province, city)
        recommendations.sort(key=_popularity_sort_key, reverse=True)
        public_rows = [_public_row(r) for r in recommendations[:TOP_RECOMMENDATIONS]]

        preview, seen = [], set()
        for row in candidates[:30]:
            key = (row.get("provider"), row.get("provider_id"), row.get("name"), _address_text(row))
            if key in seen:
                continue
            seen.add(key)
            preview.append({
                "provider": row.get("provider"),
                "name": row.get("name"),
                "address": _address_text(row),
                "status": row.get("status"),
                "rating": float(row.get("rating") or 0),
                "user_rating_count": int(row.get("user_rating_count") or 0),
            })

        successful = [k for k, v in source_results.items() if v.get("ok")]
        return {
            "ok": bool(successful),
            "province": province,
            "city": city,
            "query": query,
            "candidate_count": len(candidates),
            "recommended_count": len(public_rows),
            "restaurants": public_rows,
            "candidate_preview": preview,
            "source_results": source_results,
            "google_api_calls": google.api_calls,
            "kakao_api_calls": kakao.api_calls,
            "public_api_calls": 0,
            "billing_note": "Google 직접 검색은 Enter당 최대 1회. 자동 재시도 없음.",
        }

    async def refresh(self, province, city, bbox=None):
        google, kakao = GooglePlacesCollector(), KakaoCollector()
        tasks, names = [], []
        if google.enabled:
            names.append("google")
            tasks.append(self._safe("google", google.collect_candidates(province, city, bbox)))
        if kakao.enabled:
            names.append("kakao")
            tasks.append(self._safe("kakao", kakao.collect_taste_candidates(province, city, bbox)))

        if not tasks:
            return {
                "ok": False,
                "province": province,
                "city": city,
                "candidate_count": 0,
                "recommended_count": 0,
                "restaurants": [],
                "source_results": {},
                "message": "맛집 후보를 찾을 데이터 소스가 설정되지 않았어.",
            }

        results = await asyncio.gather(*tasks)
        source_results, discovered = {}, []
        for name, (rows, status) in zip(names, results):
            source_results[name] = status
            discovered.extend(rows)

        # Wrong-city results are never allowed into entity merging/ranking.
        discovered = [r for r in discovered if _belongs_to_requested_region(r, city, bbox)]
        recommendations = merge_and_rank(discovered, province, city)
        recommendations.sort(key=_popularity_sort_key, reverse=True)
        recommendations = recommendations[:TOP_RECOMMENDATIONS]
        public_rows = [_public_row(r) for r in recommendations]

        source_candidate_count = sum(
            int((s.get("meta") or {}).get("candidate_count", s.get("count", 0)) or 0)
            for s in source_results.values()
        )

        self.memory_cache[self.cache_key(province, city)] = {
            "restaurants": public_rows,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": source_candidate_count,
            "recommended_count": len(public_rows),
            "source_results": source_results,
            "definition": "popularity_first_top10",
        }

        storage_error = None
        try:
            persisted = self.taste_store.replace_region(province, city, recommendations)
            storage = {"status": "persisted", "persisted": persisted}
        except Exception as exc:
            storage_error = _err(exc)
            storage = {"status": "memory_only", "persisted": 0}

        successful = [k for k, v in source_results.items() if v.get("ok")]
        failed = [k for k, v in source_results.items() if not v.get("ok")]
        result = {
            "ok": bool(successful),
            "partial": bool(successful and failed),
            "province": province,
            "city": city,
            "candidate_count": source_candidate_count,
            "recommended_count": len(public_rows),
            "verified_count": len(public_rows),
            "total_seen": len(public_rows),
            "source_results": source_results,
            "successful_sources": successful,
            "failed_sources": failed,
            "google_api_calls": google.api_calls,
            "kakao_api_calls": kakao.api_calls,
            "naver_api_calls": 0,
            "public_api_calls": 0,
            "public_master_count": 0,
            "storage": storage,
            "storage_error": storage_error,
            "restaurants": public_rows,
            "definition": "popularity_first_top10",
            "criteria": {
                "goal": "지역 음식점 전체 수집이 아니라 실제 인기/평가가 강한 TOP 10",
                "discovery": "Google POPULARITY + 광범위 맛집/유명맛집 검색 + Kakao 인기 키워드 반복 노출",
                "identity": "이름·주소·전화·좌표로 동일 업체 병합",
                "category": "추천 여부에 사용하지 않음. 화면 표시는 공급자 메타데이터 보조값일 뿐",
                "public_master": "일반 최신화에서 도시 전체 인허가 명부를 수집하지 않음",
                "social": "Instagram/블로그 수치는 현재 직접 데이터 소스가 없어 조작/추정하지 않음",
                "ai": "사용하지 않음",
            },
        }
        try:
            self.db.add_refresh_log(
                province,
                city,
                0,
                0,
                len(public_rows),
                {k: v for k, v in result.items() if k != "restaurants"},
            )
        except Exception as exc:
            result["log_error"] = _err(exc)
        return result
