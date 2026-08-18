from __future__ import annotations

from datetime import datetime, timezone

from .collectors.google_places import GooglePlacesCollector
from .taste_store import TasteStore


def _err(exc: Exception):
    return {
        "ok": False,
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
    }


def _public_row(row):
    return {k: v for k, v in row.items() if k != "raw_json" and not k.startswith("_")}


class RefreshService:
    """
    맛집 서비스의 기준:
    - 사용자 화면에는 Google 사용자평가 기준을 통과한 '검증 맛집'만 노출한다.
    - Kakao/공공데이터 전체 음식점 목록은 후보/검증 보조용이지 추천 목록으로 사용하지 않는다.
    - 사용자 클릭 시 Kakao 공간 전수검색을 하지 않는다.
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
            rows.sort(key=lambda x: (x.get("taste_score", 0), x.get("user_rating_count", 0)), reverse=True)
            return rows[:limit]

    async def refresh(self, province, city, bbox=None):
        google = GooglePlacesCollector()
        if not google.enabled:
            result = {
                "ok": False,
                "province": province,
                "city": city,
                "candidate_count": 0,
                "verified_count": 0,
                "total_seen": 0,
                "restaurants": [],
                "source_results": {
                    "google": {
                        "ok": False,
                        "source": "google",
                        "count": 0,
                        "error": {
                            "type": "NotConfigured",
                            "message": "GOOGLE_PLACES_API_KEY가 없어. 사용자 평가 기반 맛집만 보여주기 때문에 일반 음식점 목록으로 대체하지 않아.",
                        },
                    }
                },
                "message": "Google Places API 키가 필요해.",
                "storage": {"status": "unchanged", "persisted": 0},
            }
            return result

        try:
            rows, meta = await google.collect_verified(province, city, bbox=bbox)
        except Exception as exc:
            return {
                "ok": False,
                "province": province,
                "city": city,
                "candidate_count": 0,
                "verified_count": 0,
                "total_seen": 0,
                "restaurants": self.get_cached_restaurants(province, city),
                "source_results": {
                    "google": {"ok": False, "source": "google", "count": 0, "error": _err(exc)}
                },
                "message": f"Google Places 수집 실패: {exc}",
                "storage": {"status": "cached", "persisted": 0},
            }

        public_rows = [_public_row(r) for r in rows]
        self.memory_cache[self.cache_key(province, city)] = {
            "restaurants": public_rows,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": meta.get("candidate_count", 0),
            "verified_count": meta.get("verified_count", len(public_rows)),
            "google_api_calls": meta.get("api_calls", 0),
        }

        storage_error = None
        persisted = 0
        try:
            persisted = self.taste_store.replace_region(province, city, rows)
            storage = {"status": "persisted", "persisted": persisted}
        except Exception as exc:
            storage_error = _err(exc)
            storage = {"status": "memory_only", "persisted": 0}

        result = {
            "ok": True,
            "province": province,
            "city": city,
            "candidate_count": meta.get("candidate_count", 0),
            "verified_count": len(public_rows),
            "total_seen": len(public_rows),
            "google_api_calls": meta.get("api_calls", 0),
            "source_results": {
                "google": {
                    "ok": True,
                    "source": "google",
                    "count": len(public_rows),
                    "error": None,
                }
            },
            "storage": storage,
            "storage_error": storage_error,
            "restaurants": public_rows,
            "criteria": {
                "rule_a": "평점 4.4 이상 + 사용자평가 50개 이상",
                "rule_b": "평점 4.2 이상 + 사용자평가 200개 이상",
                "sort": "Bayesian 보정평점 → 사용자평가 수",
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
