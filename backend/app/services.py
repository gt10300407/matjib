from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from .collectors.google_places import GooglePlacesCollector
from .collectors.kakao import KakaoCollector, TASTE_QUERY_SPECS
from .collectors.naver_local import NaverLocalCollector
from .collectors.publicdata import PublicDataCollector
from .evidence import entity_match_score, merge_and_rank, normalize_name, same_place
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
        x = float(row.get("x")); y = float(row.get("y")); minx, miny, maxx, maxy = map(float, bbox)
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
    """Attach likely license rows without throwing the whole master into O(n²) clustering."""
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
                attached.append(inv); seen.add(iid)
    return discovered + attached


def _best_entity(rows: list[dict], query: str, city: str, bbox=None) -> dict | None:
    """Pick a canonical local entity using deterministic identity evidence only."""
    candidates = [r for r in rows if _belongs_to_requested_region(r, city, bbox)]
    if not candidates:
        return None

    def score(row):
        s = 0.0
        if _query_matches_name(query, row.get("name")):
            s += 50
        provider = row.get("provider")
        if provider == "kakao":
            s += 18
        elif provider in {"general", "rest_cafe", "bakery"}:
            s += 12
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


class RefreshService:
    """Deterministic discovery/entity-resolution/ranking service.

    AI is intentionally absent. Public licensing data is factual identity/existence
    data only; Kakao provides local POI identity; Google contributes user-rating
    evidence after an entity has been resolved as precisely as possible.
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
            ql, rows = q.lower(), []
            for item in self.memory_cache.values():
                for r in item.get("restaurants", []):
                    hay = " ".join(str(r.get(k) or "") for k in ("name", "city", "cuisine", "address")).lower()
                    if ql in hay:
                        rows.append(r)
            rows.sort(key=lambda x: (x.get("taste_score", 0), x.get("source_count", 0), x.get("user_rating_count", 0)), reverse=True)
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
            r["query_category"] = "공식정보"; r["query_text"] = f"{city} 모범음식점"; r["query_hits"] = 1
        return rows, {"candidate_count": len(rows), "api_calls": max(0, public.api_calls - before)}

    async def _public_master(self, public: PublicDataCollector, province: str, city: str):
        if self.taste_store.public_inventory_is_fresh(province, city, max_age_hours=20):
            rows = self.taste_store.get_public_inventory(province, city)
            return rows, {
                "candidate_count": len(rows), "api_calls": 0, "cached": True,
                "discovery_definition": "cached combined restaurant/cafe/bakery licensing master",
            }
        rows, meta = await public.licensed_inventory(province, city)
        if rows:
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
        rows.sort(key=lambda x: (float(x.get("taste_score") or 0), int(x.get("user_rating_count") or 0)), reverse=True)
        old = self.memory_cache.get(key, {})
        self.memory_cache[key] = {**old, "restaurants": rows, "recommended_count": len(rows)}
        return rows

    async def _live_public_matches(self, public: PublicDataCollector, province: str, city: str, query: str):
        if self.taste_store.public_inventory_is_fresh(province, city, max_age_hours=20):
            rows = self.taste_store.search_public_inventory(province, city, query, limit=50)
            return rows, {"ok": True, "source": "licenses", "count": len(rows), "meta": {"cached": True, "api_calls": 0}}
        try:
            rows = await public.verify_by_name(city, query)
            for row in rows:
                row["province"] = province
            return rows, {"ok": True, "source": "licenses", "count": len(rows), "meta": {"cached": False, "api_calls": public.api_calls}}
        except Exception as exc:
            return [], {"ok": False, "source": "licenses", "count": 0, "meta": {}, "error": _err(exc)}

    async def live_search(self, province: str, city: str, q: str, bbox=None):
        """Resolve local identity first, then spend at most one Google lookup."""
        query = q.strip()
        if not query:
            return {"ok": False, "message": "검색어가 비어 있어.", "restaurants": [], "candidate_count": 0}

        google, kakao, public = GooglePlacesCollector(), KakaoCollector(), PublicDataCollector()
        source_results = {}

        public_matches = []
        if public.enabled:
            public_matches, status = await self._live_public_matches(public, province, city, query)
            source_results["licenses"] = status

        public_seed = _best_entity(public_matches, query, city, bbox)
        kakao_query = public_seed.get("name") if public_seed else query

        kakao_rows = []
        if kakao.enabled:
            kakao_rows, kakao_status = await self._safe("kakao", kakao.search_direct(province, city, kakao_query))
            source_results["kakao"] = kakao_status
            kakao_rows = [r for r in kakao_rows if _belongs_to_requested_region(r, city, bbox)]
            kakao_rows = [r for r in kakao_rows if _query_matches_name(query, r.get("name")) or (public_seed and same_place(r, public_seed))]

        canonical = _best_entity(kakao_rows + ([public_seed] if public_seed else []), query, city, bbox)
        if canonical:
            google_query = " ".join(filter(None, [canonical.get("name"), _address_text(canonical)]))
        else:
            google_query = query

        google_rows = []
        if google.enabled:
            google_rows, google_status = await self._safe("google", google.search_direct(province, city, google_query, bbox))
            source_results["google"] = google_status
            google_rows = [r for r in google_rows if _belongs_to_requested_region(r, city, bbox)]
            if canonical:
                google_rows = [r for r in google_rows if entity_match_score(r, canonical) >= 55]
            else:
                google_rows = [r for r in google_rows if _query_matches_name(query, r.get("name"))]

        matched_public = []
        for inv in public_matches:
            if not _belongs_to_requested_region(inv, city, bbox):
                continue
            if _query_matches_name(query, inv.get("name")) or any(entity_match_score(inv, ext) >= 55 for ext in kakao_rows + google_rows):
                matched_public.append(inv)

        candidates = kakao_rows + google_rows + matched_public
        recommendations = merge_and_rank(candidates, province, city)
        public_rows = [_public_row(r) for r in recommendations]
        if public_rows:
            self._merge_cache_rows(province, city, public_rows)
            try:
                self.taste_store.upsert_region(province, city, recommendations)
            except Exception:
                pass

        preview, seen_preview = [], set()
        for row in candidates[:30]:
            key = (row.get("provider"), row.get("provider_id"), row.get("name"), _address_text(row))
            if key in seen_preview:
                continue
            seen_preview.add(key)
            preview.append({
                "provider": row.get("provider"), "name": row.get("name"), "address": _address_text(row),
                "status": row.get("status"), "rating": float(row.get("rating") or 0),
                "user_rating_count": int(row.get("user_rating_count") or 0),
            })

        successful = [k for k, v in source_results.items() if v.get("ok")]
        return {
            "ok": bool(successful), "province": province, "city": city, "query": query,
            "resolved_query": google_query, "resolved_entity": {
                "name": canonical.get("name"), "address": _address_text(canonical), "provider": canonical.get("provider")
            } if canonical else None,
            "candidate_count": len(candidates), "recommended_count": len(public_rows), "restaurants": public_rows,
            "candidate_preview": preview, "source_results": source_results,
            "google_api_calls": google.api_calls, "kakao_api_calls": kakao.api_calls, "public_api_calls": public.api_calls,
            "billing_note": "Google 실시간 검색은 기존과 동일하게 Enter당 최대 1회",
        }

    async def refresh(self, province, city, bbox=None):
        google, kakao, naver = GooglePlacesCollector(), KakaoCollector(), NaverLocalCollector()
        public_master, public_excellent = PublicDataCollector(), PublicDataCollector()
        tasks, names = [], []
        if google.enabled:
            names.append("google"); tasks.append(self._safe("google", google.collect_candidates(province, city, bbox)))
        if kakao.enabled:
            names.append("kakao"); tasks.append(self._safe("kakao", kakao.collect_taste_candidates(province, city, bbox)))
        if naver.enabled:
            specs = [(cat, tpl.format(city=city)) for cat, tpl, _ in TASTE_QUERY_SPECS]
            names.append("naver"); tasks.append(self._safe("naver", naver.search(province, city, specs)))
        if public_master.enabled:
            names.append("licenses"); tasks.append(self._safe("licenses", self._public_master(public_master, province, city)))
        if public_excellent.enabled:
            names.append("excellent"); tasks.append(self._safe("excellent", self._excellent(public_excellent, province, city)))

        if not tasks:
            return {"ok": False, "province": province, "city": city, "candidate_count": 0, "recommended_count": 0, "restaurants": [], "source_results": {}, "message": "맛집 후보를 찾을 데이터 소스가 설정되지 않았어."}

        results = await asyncio.gather(*tasks)
        source_results, discovered, inventory = {}, [], []
        for name, (rows, status) in zip(names, results):
            source_results[name] = status
            if name == "licenses":
                inventory = rows
            else:
                discovered.extend(rows)

        # Hard region boundary before entity matching.
        discovered = [r for r in discovered if _belongs_to_requested_region(r, city, bbox)]
        all_rows = _attach_public_inventory(discovered, inventory)
        recommendations = merge_and_rank(all_rows, province, city)
        public_rows = [_public_row(r) for r in recommendations]
        source_candidate_count = sum(int((s.get("meta") or {}).get("candidate_count", s.get("count", 0)) or 0) for s in source_results.values())

        self.memory_cache[self.cache_key(province, city)] = {
            "restaurants": public_rows, "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": source_candidate_count, "recommended_count": len(public_rows), "source_results": source_results,
        }

        storage_error = None
        try:
            persisted = self.taste_store.replace_region(province, city, recommendations)
            storage = {"status": "persisted", "persisted": persisted}
        except Exception as exc:
            storage_error = _err(exc); storage = {"status": "memory_only", "persisted": 0}

        successful = [k for k, v in source_results.items() if v.get("ok")]
        failed = [k for k, v in source_results.items() if not v.get("ok")]
        result = {
            "ok": bool(successful), "partial": bool(successful and failed), "province": province, "city": city,
            "candidate_count": source_candidate_count, "recommended_count": len(public_rows), "verified_count": len(public_rows),
            "total_seen": len(public_rows), "source_results": source_results, "successful_sources": successful, "failed_sources": failed,
            "google_api_calls": google.api_calls, "kakao_api_calls": kakao.api_calls, "naver_api_calls": naver.api_calls,
            "public_api_calls": public_master.api_calls + public_excellent.api_calls,
            "public_master_count": len(inventory), "storage": storage, "storage_error": storage_error, "restaurants": public_rows,
            "definition": "deterministic_entity_resolution_recommendation",
            "criteria": {
                "identity": "이름·주소·전화·좌표의 결정론적 점수로 동일 업체 판정",
                "public_master": "일반음식점·휴게음식점·제과점 인허가 명부는 존재/상호/주소 확인용",
                "google": "사용자 평점/평가 수는 추천 근거이며 업체 identity의 유일한 기준이 아님",
                "ai": "사용하지 않음",
            },
        }
        try:
            self.db.add_refresh_log(province, city, 0, 0, len(public_rows), {k: v for k, v in result.items() if k != "restaurants"})
        except Exception as exc:
            result["log_error"] = _err(exc)
        return result
