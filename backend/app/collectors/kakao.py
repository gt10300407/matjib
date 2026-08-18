from __future__ import annotations

import asyncio
import os

import httpx

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

TASTE_QUERY_SPECS = [
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
    ("카페", "{city} 카페", "CE7"),
    ("디저트", "{city} 디저트 맛집", None),
    ("디저트", "{city} 베이커리 맛집", None),
]


class KakaoCollector:
    def __init__(self):
        self.key=os.getenv("KAKAO_REST_API_KEY","").strip(); self.api_calls=0; self.last_mode="none"
    @property
    def enabled(self): return bool(self.key)
    def _headers(self): return {"Authorization":f"KakaoAK {self.key}"}

    def _normalize(self,d:dict,province:str,city:str,category:str|None=None,query:str|None=None):
        return {
            "provider":"kakao","provider_id":str(d.get("id") or ""),"province":province,"city":city,
            "name":d.get("place_name"),"category":category or d.get("category_name"),
            "business_type":d.get("category_group_name") or "장소","address":d.get("address_name"),
            "road_address":d.get("road_address_name"),"phone":d.get("phone"),"x":d.get("x"),"y":d.get("y"),
            "place_url":d.get("place_url"),"status":"Kakao 검색 노출","verified_public":False,
            "query_category":category,"query_text":query,"query_hits":1,"raw_json":d,
        }

    def _belongs_to_city(self,d:dict,city:str):
        addr=f"{d.get('address_name','')} {d.get('road_address_name','')}".strip()
        return (not addr) or (city in addr)

    async def _get(self,client,params):
        self.api_calls+=1
        r=await client.get(KEYWORD_URL,headers=self._headers(),params=params)
        if r.status_code!=200:
            try: body=r.json(); msg=body.get("msg") or body.get("message") or str(body)
            except Exception: msg=r.text[:300]
            raise RuntimeError(f"Kakao HTTP {r.status_code}: {msg}")
        return r.json()

    async def _query_once(self,client,province,city,spec):
        category,template,code=spec; query=template.format(city=city)
        params={"query":query,"page":1,"size":15,"sort":"accuracy"}
        if code: params["category_group_code"]=code
        data=await self._get(client,params)
        rows=[]
        for d in data.get("documents",[]):
            if self._belongs_to_city(d,city): rows.append(self._normalize(d,province,city,category,query))
        return rows

    async def collect_taste_candidates(self,province:str,city:str,bbox=None):
        if not self.enabled: return [],{"candidate_count":0,"api_calls":0}
        self.api_calls=0; self.last_mode="taste_queries"
        timeout=httpx.Timeout(10.0,connect=4.0); sem=asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True) as client:
            async def run(spec):
                async with sem: return await self._query_once(client,province,city,spec)
            batches=await asyncio.gather(*(run(spec) for spec in TASTE_QUERY_SPECS))
        rows=[r for batch in batches for r in batch]
        return rows,{"candidate_count":len(rows),"api_calls":self.api_calls}

    async def collect_places(self,province:str,city:str,bbox=None):
        # Compatibility: v3 intentionally avoids the old hundreds-of-calls spatial crawl.
        rows,_=await self.collect_taste_candidates(province,city,bbox)
        found={}
        for row in rows: found[row["provider_id"]]=row
        return list(found.values())

    async def search_restaurants(self,province:str,city:str,pages:int=3):
        return await self.collect_places(province,city,bbox=None)
