from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from .collectors.google_places import GooglePlacesCollector
from .collectors.kakao import KakaoCollector, TASTE_QUERY_SPECS
from .collectors.naver_local import NaverLocalCollector
from .collectors.publicdata import PublicDataCollector, normalize_general_row
from .evidence import merge_and_rank, normalize_name, same_place
from .taste_store import TasteStore


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
    q = normalize_name(query)
    n = normalize_name(name)
    if not q or not n:
        return False
    if q in n or n in q:
        return True
    terms = [normalize_name(x) for x in re.split(r"\s+", query.strip()) if normalize_name(x)]
    return bool(terms) and all(t in n for t in terms)


def _attach_public_inventory(discovered: list[dict], inventory: list[dict]) -> list[dict]:
    """Attach matching official-license rows without feeding the whole master into O(n²) clustering."""
    by_name: dict[str, list[dict]] = {}
    by_phone: dict[str, list[dict]] = {}
    for row in inventory:
        nk = normalize_name(row.get("name"))
        if nk:
            by_name.setdefault(nk, []).append(row)
        pk = _phone_key(row.get("phone"))
        if len(pk) >= 8:
            by_phone.setdefault(pk, []).append(row)

    attached = []
    seen = set()
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
            exact_phone = len(pk) >= 8 and pk == _phone_key(inv.get("phone"))
            if exact_phone or same_place(row, inv):
                attached.append(inv)
                seen.add(iid)
    return discovered + attached


class RefreshService:
    """Evidence-aggregated taste service with an official restaurant master.

    The Ministry of the Interior and Safety general-restaurant dataset is used as
    a city-wide existence/name/address master. It is not treated as taste proof by
    itself. Google/Kakao/etc. still decide recommendation strength.
    """

    def __init__(self, db):
        self.db = db
        self.memory_cache = {}
        self.taste_store = TasteStore(db.path)

    def cache_key(self, province, city):
        return (province.strip(), city.strip())

    def get_cached_restaurants(self, province, city, limit=300):
        item = self.memory_cache.get(self.cache_key(province, city))
        if item:
            return list(item.get("restaurants", []))[:limit]
        try:
            return self.taste_store.get_region(province, city, limit)
        except Exception:
            return []

    def get_cached_meta(self, province, city):
        return self.memory_cache.get(self.cache_key(province, city))

    def count_verified(self, province=None, city=None):
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
            ql = q.lower()
            rows = []
            for item in self.memory_cache.values():
                for r in item.get("restaurants", []):
                    hay = " ".join(str(r.get(k) or "") for k in ("name", "city", "cuisine", "address")).lower()
                    if ql in hay:
                        rows.append(r)
            rows.sort(
                key=lambda x: (x.get("taste_score", 0), x.get("source_count", 0), x.get("user_rating_count", 0)),
                reverse=True,
            )
            return rows[:limit]

    async def _safe(self, name, coro):
        try:
            rows, meta = await coro
            return rows, {"ok": True, "source": name, "count": len(rows), "meta": meta, "error": None}
        except Exception as exc:
            return [], {"ok": False, "source": name, "count": 0, "meta": {}, "error": _err(exc)}

    async def _excellent(self, public, province, city):
        before = public.api_calls
        rows = await public.excellent(province, city)
        for r in rows:
            r["query_category"] = "공식정보"
            r["query_text"] = f"{city} 모범음식점"
            r["query_hits"] = 1
        return rows, {"candidate_count": len(rows), "api_calls": max(0, public.api_calls - before)}

    async def _public_master(self, public: PublicDataCollector, province: str, city: str):
        if self.taste_store.public_inventory_is_fresh(province, city, max_age_hours=20):
            rows = self.taste_store.get_public_inventory(province, city)
            return rows, {
                "candidate_count": len(rows),
                "api_calls": 0,
                "pages": 0,
                "cached": True,
                "discovery_definition": "cached city-wide general restaurant licensing master",
            }
        rows, meta = await public.general_inventory(province, city)
        self.taste_store.replace_public_inventory(province, city, rows)
        return rows, {**meta, "cached": False}

    def _merge_cache_rows(self, province: str, city: str, new_rows: list[dict]):
        key = self.cache_key(province, city)
        current = list(self.memory_cache.get(key, {}).get("restaurants", []))
        if not current:
            try:
                current = self.taste_store.get_region(province, city, 1000)
            except Exception:
                current = []
        merged = {str(r.get("provider_id")): r for r in current if r.get("provider_id")}
        for row in new_rows:
            pid = str(row.get("provider_id") or "")
            if pid:
                merged[pid] = row
        rows = list(merged.values())
        rows.sort(
            key=lambda x: (float(x.get("taste_score") or 0), int(x.get("user_rating_count") or 0)), reverse=True
        )
        old = self.memory_cache.get(key, {})
        self.memory_cache[key] = {**old, "restaurants": rows, "recommended_count": len(rows)}
        return rows

    async def _live_public_matches(self, public: PublicDataCollector, province: str, city: str, query: str):
        if self.taste_store.public_inventory_is_fresh(province, city, max_age_hours=20):
            rows = self.taste_store.search_public_inventory(province, city, query, limit=30)
            return rows, {"ok": True, "source": "general", "count": len(rows), "meta": {"cached": True, "api_calls": 0}}
        try:
            raw = await public.verify_by_name(city, query)
            rows = [normalize_general_row(d, province, city) for d in raw]
            return rows, {
                "ok": True,
                "source": "general",
                "count": len(rows),
                "meta": {"cached": False, "api_calls": public.api_calls},
            }
        except Exception as exc:
            return [], {"ok": False, "source": "general", "count": 0, "meta": {}, "error": _err(exc)}

    async def live_search(self, province: str, city: str, q: str, bbox=None):
        """Verify a user query with strict region/name filtering.

        Public-data restaurant matches are used as a factual master and alias aid.
        Kakao and Google still make at most one request each per Enter press.
        """
        query = q.strip()
        if not query:
            return {"ok": False, "message": "검색어가 비어 있어.", "restaurants": [], "candidate_count": 0}

        google = GooglePlacesCollector()
        kakao = KakaoCollector()
        public = PublicDataCollector()

        public_matches = []
        source_results = {}
        if public.enabled:
            public_matches, public_status = await self._live_public_matches(public, province, city, query)
            source_results["general"] = public_status

        # If the public master yields one exact/contained business-name match, use
        # that canonical name for the single external verification request.
        strong_public = [r for r in public_matches if _query_matches_name(query, r.get("name"))]
        external_query = strong_public[0].get("name") if len(strong_public) == 1 else query

        tasks = []
        names = []
        if kakao.enabled:
            names.append("kakao")
            tasks.append(self._safe("kakao", kakao.search_direct(province, city, external_query)))
        if google.enabled:
            names.append("google")
            tasks.append(self._safe("google", google.search_direct(province, city, external_query, bbox)))

        if tasks:
            results = await asyncio.gather(*tasks)
        else:
            results = []

        external_candidates = []
        for name, (rows, status) in zip(names, results):
            source_results[name] = status
            external_candidates.extend(rows)

        def relevant(row: dict) -> bool:
            if not _belongs_to_requested_region(row, city, bbox):
                return False
            if _query_matches_name(query, row.get("name")) or _query_matches_name(external_query, row.get("name")):
                return True
            return any(same_place(row, inv) for inv in public_matches[:30])

        external_candidates = [r for r in external_candidates if relevant(r)]
        matched_public = []
        for inv in public_matches:
            if not _belongs_to_requested_region(inv, city, bbox):
                continue
            if _query_matches_name(query, inv.get("name")) or any(same_place(inv, ext) for ext in external_candidates):
                matched_public.append(inv)

        candidates = external_candidates + matched_public
        recommendations = merge_and_rank(candidates, province, city)
        public_rows = [_public_row(r) for r in recommendations]
        if public_rows:
            self._merge_cache_rows(province, city, public_rows)
            try:
                self.taste_store.upsert_region(province, city, recommendations)
            except Exception:
                pass

        preview = []
        seen_preview = set()
        for row in candidates[:30]:
            key = (row.get("provider"), row.get("provider_id"), row.get("name"), _address_text(row))
            if key in seen_preview:
                continue
            seen_preview.add(key)
            preview.append(
                {
                    "provider": row.get("provider"),
                    "name": row.get("name"),
                    "address": _address_text(row),
                    "status": row.get("status"),
                    "rating": float(row.get("rating") or 0),
                    "user_rating_count": int(row.get("user_rating_count") or 0),
                }
            )

        successful = [k for k, v in source_results.items() if v.get("ok")]
        return {
            "ok": bool(successful),
            "province": province,
            "city": city,
            "query": query,
            "resolved_query": external_query,
            "candidate_count": len(candidates),
            "recommended_count": len(public_rows),
            "restaurants": public_rows,
            "candidate_preview": preview,
            "source_results": source_results,
            "google_api_calls": google.api_calls,
            "kakao_api_calls": kakao.api_calls,
            "public_api_calls": public.api_calls,
            "billing_note": "Google 실시간 검색은 Enter를 눌렀을 때만 최대 1회 호출",
        }

    async def refresh(self, province, city, bbox=None):
        google = GooglePlacesCollector()
        kakao = KakaoCollector()
        naver = NaverLocalCollector()
        public_master = PublicDataCollector()
        public_excellent = PublicDataCollector()

        tasks = []
        names = []
        if google.enabled:
            names.append("google")
            tasks.append(self._safe("google", google.collect_candidates(province, city, bbox)))
        if kakao.enabled:
            names.append("kakao")
            tasks.append(self._safe("kakao", kakao.collect_taste_candidates(province, city, bbox)))
        if naver.enabled:
            specs = [(cat, tpl.format(city=city)) for cat, tpl, _ in TASTE_QUERY_SPECS]
            names.append("naver")
            tasks.append(self._safe("naver", naver.search(province, city, specs)))
        if public_master.enabled:
            names.append("general")
            tasks.append(self._safe("general", self._public_master(public_master, province, city)))
        if public_excellent.enabled:
            names.append("excellent")
            tasks.append(self._safe("excellent", self._excellent(public_excellent, province, city)))

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
        source_results = {}
        discovered = []
        inventory = []
        for name, (rows, status) in zip(names, results):
            source_results[name] = status
            if name == "general":
                inventory = rows
            else:
                discovered.extend(rows)

        # The complete public master is kept outside merge_and_rank to avoid both
        # O(n²) clustering and the false claim that licensing alone means '맛집'.
        all_rows = _attach_public_inventory(discovered, inventory)
        recommendations = merge_and_rank(all_rows, province, city)
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
            "public_inventory_count": len(inventory),
            "source_results": source_results,
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
            "public_inventory_count": len(inventory),
            "recommended_count": len(public_rows),
            "verified_count": len(public_rows),
            "total_seen": len(public_rows),
            "source_results": source_results,
            "successful_sources": successful,
            "failed_sources": failed,
            "google_api_calls": google.api_calls,
            "kakao_api_calls": kakao.api_calls,
            "naver_api_calls": naver.api_calls,
            "public_api_calls": public_master.api_calls + public_excellent.api_calls,
            "storage": storage,
            "storage_error": storage_error,
            "restaurants": public_rows,
            "definition": "public_master_plus_evidence_recommendation",
            "criteria": {
                "public_master": "공공 일반음식점 인허가 명부는 존재/상호/주소 후보 마스터로 사용하며 맛집 근거 자체로는 사용하지 않음",
                "google": "사용자 평점/평가 수는 강한 근거지만 필수 조건 아님",
                "local_repeat": "세부 메뉴 검색에서 반복 노출되면 Google 미노출이어도 추천 가능",
                "inventory": "Kakao 음식점/카페 4x4 공간망 + 공공 음식점 마스터를 함께 사용",
                "score": "추천 근거 점수 0~100이며 맛집일 확률(%)이 아님",
            },
        }
        try:
            self.db.add_refresh_log(
                province, city, 0, 0, len(public_rows), {k: v for k, v in result.items() if k != "restaurants"}
            )
        except Exception as exc:
            result["log_error"] = _err(exc)
        return result
