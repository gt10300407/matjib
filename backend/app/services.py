from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .collectors.kakao import KakaoCollector
from .collectors.publicdata import PublicDataCollector

def _err(exc: Exception):
    return {
        "ok": False,
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
    }

def _public_row(row):
    return {
        k:v for k,v in row.items()
        if k != "raw_json" and not k.startswith("_")
    }

class RefreshService:
    def __init__(self, db):
        self.db = db
        self.memory_cache = {}

    def cache_key(self, province, city):
        return (province.strip(), city.strip())

    def get_cached_restaurants(self, province, city):
        item=self.memory_cache.get(self.cache_key(province,city))
        return list(item.get("restaurants",[])) if item else []

    def get_cached_meta(self, province, city):
        return self.memory_cache.get(self.cache_key(province,city))

    def memory_stats(self, province=None, city=None):
        unique={}
        for (p,c),item in self.memory_cache.items():
            if province and p != province:
                continue
            if city and c != city:
                continue
            for r in item.get("restaurants",[]):
                key=(r.get("provider"),r.get("provider_id"))
                unique[key]=r
        return len(unique)

    async def _safe(self, source_name, coro):
        try:
            rows = await coro
            return rows, {"ok": True,"source": source_name,"count": len(rows),"error": None}
        except Exception as exc:
            return [], {"ok": False,"source": source_name,"count": 0,"error": _err(exc)}

    async def refresh(self, province, city, bbox=None):
        kakao=KakaoCollector()
        public=PublicDataCollector()

        kakao_task = (
            self._safe("kakao", kakao.collect_places(province,city,bbox=bbox))
            if kakao.enabled else
            asyncio.sleep(0,result=([],{"ok":False,"source":"kakao","count":0,"error":{"type":"NotConfigured","message":"KAKAO_REST_API_KEY가 없어."}}))
        )

        excellent_task = (
            self._safe("excellent", public.excellent(province,city))
            if public.enabled else
            asyncio.sleep(0,result=([],{"ok":False,"source":"excellent","count":0,"error":{"type":"NotConfigured","message":"DATA_GO_KR_SERVICE_KEY가 없어."}}))
        )

        (kakao_rows,kakao_result),(excellent_rows,excellent_result)=await asyncio.gather(kakao_task,excellent_task)

        dedup={}
        for row in excellent_rows + kakao_rows:
            key=(row.get("provider"),row.get("provider_id"))
            dedup[key]=row
        rows=list(dedup.values())

        public_rows=[_public_row(r) for r in rows]
        self.memory_cache[self.cache_key(province,city)]={
            "restaurants":public_rows,
            "refreshed_at":datetime.now(timezone.utc).isoformat(),
            "kakao_mode":getattr(kakao,"last_mode","unknown"),
            "kakao_api_calls":getattr(kakao,"api_calls",0),
        }

        storage_error=None
        storage={"status":"memory_only","persisted":0,"new":0,"changed":0,"same":0}
        if rows:
            try:
                if hasattr(self.db, "upsert_many"):
                    persisted=self.db.upsert_many(rows)
                else:
                    counts={"new":0,"changed":0,"same":0,"persisted":0}
                    for row in rows:
                        state=self.db.upsert_restaurant(row)
                        counts[state]=counts.get(state,0)+1
                        counts["persisted"]+=1
                    persisted=counts
                storage={"status":"persisted",**persisted}
            except Exception as exc:
                storage_error=_err(exc)

        verified=0
        verify_error=None
        if public.enabled and kakao_rows and storage["status"]=="persisted":
            for row in kakao_rows[:8]:
                try:
                    matches=await public.verify_by_name(city,row["name"])
                    if matches:
                        self.db.mark_kakao_public_verified(row["provider_id"])
                        verified+=1
                except Exception as exc:
                    verify_error=_err(exc)
                    break

        source_results={
            "kakao":kakao_result,
            "excellent":excellent_result,
            "general_verify":{"ok":verify_error is None,"source":"general_verify","count":verified,"error":verify_error},
        }

        successful_sources=[k for k,v in source_results.items() if v.get("ok") and k in {"kakao","excellent"}]
        failed_sources=[k for k,v in source_results.items() if not v.get("ok") and (v.get("error") or {}).get("type")!="NotConfigured"]

        result={
            "ok":bool(successful_sources),
            "partial":bool(successful_sources and failed_sources),
            "province":province,
            "city":city,
            "new_count":storage.get("new",0),
            "changed_count":storage.get("changed",0),
            "closed_suspect":0,
            "total_seen":len(rows),
            "public_verified":verified,
            "kakao_mode":getattr(kakao,"last_mode","unknown"),
            "kakao_api_calls":getattr(kakao,"api_calls",0),
            "source_results":source_results,
            "successful_sources":successful_sources,
            "failed_sources":failed_sources,
            "storage":storage,
            "storage_error":storage_error,
            "restaurants":public_rows,
        }

        try:
            self.db.add_refresh_log(province,city,storage.get("new",0),storage.get("changed",0),len(rows),{k:v for k,v in result.items() if k!="restaurants"})
        except Exception as exc:
            result["log_error"]=_err(exc)

        return result
