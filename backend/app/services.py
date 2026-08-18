from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .collectors.google_places import GooglePlacesCollector
from .collectors.kakao import KakaoCollector, TASTE_QUERY_SPECS
from .collectors.naver_local import NaverLocalCollector
from .collectors.publicdata import PublicDataCollector
from .evidence import merge_and_rank
from .taste_store import TasteStore


def _err(exc: Exception):
    return {"ok":False,"type":type(exc).__name__,"message":str(exc) or type(exc).__name__}


def _public_row(row):
    return {k:v for k,v in row.items() if k!="raw_json" and not k.startswith("_")}


class RefreshService:
    """Evidence-aggregated taste service.

    Google is one evidence source, never a mandatory gate. Candidates are the union
    of Google, Kakao menu-level searches, optional Naver local search, and official
    excellent-restaurant data. Only candidates with enough evidence are surfaced.
    """

    def __init__(self,db):
        self.db=db; self.memory_cache={}; self.taste_store=TasteStore(db.path)

    def cache_key(self,province,city): return (province.strip(),city.strip())
    def get_cached_restaurants(self,province,city,limit=300):
        item=self.memory_cache.get(self.cache_key(province,city))
        if item: return list(item.get("restaurants",[]))[:limit]
        try: return self.taste_store.get_region(province,city,limit)
        except Exception: return []
    def get_cached_meta(self,province,city): return self.memory_cache.get(self.cache_key(province,city))
    def count_verified(self,province=None,city=None):
        try: return self.taste_store.count(province,city)
        except Exception:
            unique={}
            for (p,c),item in self.memory_cache.items():
                if province and p!=province: continue
                if city and c!=city: continue
                for r in item.get("restaurants",[]): unique[r.get("provider_id")]=r
            return len(unique)
    def memory_stats(self,province=None,city=None): return self.count_verified(province,city)
    def search_verified(self,q,limit=100):
        try: return self.taste_store.search(q,limit)
        except Exception:
            ql=q.lower(); rows=[]
            for item in self.memory_cache.values():
                for r in item.get("restaurants",[]):
                    hay=" ".join(str(r.get(k) or "") for k in ("name","city","cuisine","address")).lower()
                    if ql in hay: rows.append(r)
            rows.sort(key=lambda x:(x.get("taste_score",0),x.get("source_count",0),x.get("user_rating_count",0)),reverse=True)
            return rows[:limit]

    async def _safe(self,name,coro):
        try:
            rows,meta=await coro
            return rows,{"ok":True,"source":name,"count":len(rows),"meta":meta,"error":None}
        except Exception as exc:
            return [],{"ok":False,"source":name,"count":0,"meta":{},"error":_err(exc)}

    async def _excellent(self,public,province,city):
        rows=await public.excellent(province,city)
        for r in rows:
            r["query_category"]="공식정보"; r["query_text"]=f"{city} 모범음식점"; r["query_hits"]=1
        return rows,{"candidate_count":len(rows),"api_calls":1}

    async def refresh(self,province,city,bbox=None):
        google=GooglePlacesCollector(); kakao=KakaoCollector(); naver=NaverLocalCollector(); public=PublicDataCollector()
        tasks=[]; names=[]
        if google.enabled:
            names.append("google"); tasks.append(self._safe("google",google.collect_candidates(province,city,bbox)))
        if kakao.enabled:
            names.append("kakao"); tasks.append(self._safe("kakao",kakao.collect_taste_candidates(province,city,bbox)))
        if naver.enabled:
            specs=[(cat,tpl.format(city=city)) for cat,tpl,_ in TASTE_QUERY_SPECS]
            names.append("naver"); tasks.append(self._safe("naver",naver.search(province,city,specs)))
        if public.enabled:
            names.append("excellent"); tasks.append(self._safe("excellent",self._excellent(public,province,city)))

        if not tasks:
            return {"ok":False,"province":province,"city":city,"candidate_count":0,"recommended_count":0,"restaurants":[],"source_results":{},"message":"맛집 후보를 찾을 데이터 소스가 설정되지 않았어."}

        results=await asyncio.gather(*tasks)
        source_results={}; all_rows=[]
        for name,(rows,status) in zip(names,results):
            source_results[name]=status; all_rows.extend(rows)

        recommendations=merge_and_rank(all_rows,province,city)
        public_rows=[_public_row(r) for r in recommendations]
        source_candidate_count=sum(int((s.get("meta") or {}).get("candidate_count",s.get("count",0)) or 0) for s in source_results.values())

        self.memory_cache[self.cache_key(province,city)]={
            "restaurants":public_rows,"refreshed_at":datetime.now(timezone.utc).isoformat(),
            "candidate_count":source_candidate_count,"recommended_count":len(public_rows),
            "source_results":source_results,
        }

        storage_error=None
        try:
            persisted=self.taste_store.replace_region(province,city,recommendations)
            storage={"status":"persisted","persisted":persisted}
        except Exception as exc:
            storage_error=_err(exc); storage={"status":"memory_only","persisted":0}

        successful=[k for k,v in source_results.items() if v.get("ok")]
        failed=[k for k,v in source_results.items() if not v.get("ok")]
        result={
            "ok":bool(successful),"partial":bool(successful and failed),"province":province,"city":city,
            "candidate_count":source_candidate_count,"recommended_count":len(public_rows),"verified_count":len(public_rows),
            "total_seen":len(public_rows),"source_results":source_results,"successful_sources":successful,"failed_sources":failed,
            "google_api_calls":google.api_calls,"kakao_api_calls":kakao.api_calls,"naver_api_calls":naver.api_calls,
            "storage":storage,"storage_error":storage_error,"restaurants":public_rows,
            "definition":"evidence_aggregated_recommendation",
            "criteria":{
                "google":"사용자 평점/평가 수는 강한 근거지만 필수 조건 아님",
                "local_repeat":"세부 메뉴 검색에서 반복 노출되면 Google 미노출이어도 추천 가능",
                "cross_source":"Kakao/Google/Naver/공식정보의 교차 출처를 가산",
                "score":"추천 근거 점수 0~100이며 맛집일 확률(%)이 아님",
            },
        }
        try:
            self.db.add_refresh_log(province,city,0,0,len(public_rows),{k:v for k,v in result.items() if k!="restaurants"})
        except Exception as exc: result["log_error"]=_err(exc)
        return result
