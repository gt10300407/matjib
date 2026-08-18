from __future__ import annotations

import asyncio
import math
import os
from typing import Any

import httpx

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id","places.displayName","places.formattedAddress","places.location",
    "places.rating","places.userRatingCount","places.primaryType","places.types",
    "places.googleMapsUri","places.businessStatus",
])

SEARCH_SPECS = [
    ("전체", "{city} 맛집", "restaurant"),
    ("한식", "{city} 한식 맛집", "korean_restaurant"),
    ("중식", "{city} 중식 맛집", "chinese_restaurant"),
    ("일식", "{city} 일식 맛집", "japanese_restaurant"),
    ("양식", "{city} 양식 맛집", "restaurant"),
    ("아시아", "{city} 아시아 음식 맛집", "asian_restaurant"),
    ("분식", "{city} 분식 맛집", "restaurant"),
    ("카페", "{city} 카페", "cafe"),
    ("디저트", "{city} 디저트 베이커리", "dessert_shop"),
]

TYPE_TO_CUISINE = {
    "korean_restaurant":"한식","korean_barbecue_restaurant":"한식",
    "chinese_restaurant":"중식","chinese_noodle_restaurant":"중식","dim_sum_restaurant":"중식",
    "japanese_restaurant":"일식","sushi_restaurant":"일식","ramen_restaurant":"일식","tonkatsu_restaurant":"일식","japanese_curry_restaurant":"일식","japanese_izakaya_restaurant":"일식",
    "italian_restaurant":"양식","french_restaurant":"양식","american_restaurant":"양식","western_restaurant":"양식","steak_house":"양식","pizza_restaurant":"양식",
    "asian_restaurant":"아시아","thai_restaurant":"아시아","vietnamese_restaurant":"아시아","indian_restaurant":"아시아","indonesian_restaurant":"아시아",
    "cafe":"카페","coffee_shop":"카페","tea_house":"카페",
    "bakery":"디저트","dessert_shop":"디저트","dessert_restaurant":"디저트","cake_shop":"디저트","pastry_shop":"디저트",
}


def _valid_bbox(bbox):
    if not bbox or len(bbox) != 4: return False
    try: minx,miny,maxx,maxy=map(float,bbox)
    except Exception: return False
    return -180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90


def _bayesian_score(rating: float, reviews: int, prior: float = 4.2, weight: int = 100) -> float:
    return (reviews/(reviews+weight))*rating + (weight/(reviews+weight))*prior


def _is_verified(rating: float, reviews: int) -> bool:
    # Legacy strong-Google rule retained as one evidence path, not the only gate.
    return (rating >= 4.4 and reviews >= 50) or (rating >= 4.2 and reviews >= 200)


def _classify(types: list[str], query_category: str) -> str:
    for t in types:
        if t in TYPE_TO_CUISINE: return TYPE_TO_CUISINE[t]
    if query_category != "전체": return query_category
    return "기타"


class GooglePlacesCollector:
    def __init__(self):
        self.key=os.getenv("GOOGLE_PLACES_API_KEY","").strip(); self.api_calls=0
    @property
    def enabled(self): return bool(self.key)

    async def _search_one(self, client:httpx.AsyncClient, province:str, city:str, bbox, spec):
        category,query_tpl,included_type=spec
        query=query_tpl.format(city=city)
        body:dict[str,Any]={"textQuery":query,"languageCode":"ko","regionCode":"KR","rankPreference":"RELEVANCE","pageSize":20}
        if included_type: body["includedType"]=included_type
        if _valid_bbox(bbox):
            minx,miny,maxx,maxy=map(float,bbox)
            body["locationRestriction"]={"rectangle":{"low":{"latitude":miny,"longitude":minx},"high":{"latitude":maxy,"longitude":maxx}}}
        self.api_calls+=1
        r=await client.post(SEARCH_URL,headers={"X-Goog-Api-Key":self.key,"X-Goog-FieldMask":FIELD_MASK,"Content-Type":"application/json"},json=body)
        if r.status_code!=200:
            try: detail=r.json()
            except Exception: detail=r.text[:500]
            raise RuntimeError(f"Google Places HTTP {r.status_code}: {detail}")
        out=[]
        for p in r.json().get("places",[]):
            if p.get("businessStatus") not in (None,"OPERATIONAL"): continue
            rating=float(p.get("rating") or 0); reviews=int(p.get("userRatingCount") or 0)
            types=list(p.get("types") or []); cuisine=_classify(types,category); loc=p.get("location") or {}
            out.append({
                "provider":"google","provider_id":str(p.get("id") or ""),"province":province,"city":city,
                "name":((p.get("displayName") or {}).get("text") or "이름없음"),"category":cuisine,"cuisine":cuisine,
                "business_type":p.get("primaryType") or "restaurant","primary_type":p.get("primaryType"),
                "address":p.get("formattedAddress"),"road_address":p.get("formattedAddress"),"phone":None,
                "x":str(loc.get("longitude")) if loc.get("longitude") is not None else None,
                "y":str(loc.get("latitude")) if loc.get("latitude") is not None else None,
                "place_url":p.get("googleMapsUri"),"status":"Google 검색 노출","verified_public":False,
                "rating":rating,"user_rating_count":reviews,"taste_score":round(_bayesian_score(rating,reviews),4) if reviews else 0,
                "query_category":category,"query_text":query,"query_hits":1,"raw_json":p,
            })
        return out

    async def collect_candidates(self, province:str, city:str, bbox=None):
        if not self.enabled: return [],{"candidate_count":0,"api_calls":0}
        timeout=httpx.Timeout(12.0,connect=5.0); semaphore=asyncio.Semaphore(5)
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True) as client:
            async def run(spec):
                async with semaphore: return await self._search_one(client,province,city,bbox,spec)
            batches=await asyncio.gather(*(run(spec) for spec in SEARCH_SPECS))
        rows=[r for batch in batches for r in batch]
        return rows,{"candidate_count":len(rows),"api_calls":self.api_calls}

    async def collect_verified(self, province:str, city:str, bbox=None):
        # Backward compatibility for older callers/tests.
        rows,meta=await self.collect_candidates(province,city,bbox)
        found={}
        for row in rows:
            if not _is_verified(float(row.get("rating") or 0),int(row.get("user_rating_count") or 0)): continue
            pid=row["provider_id"]
            if pid in found: found[pid]["query_hits"]+=1
            else: found[pid]=row
        out=list(found.values())
        out.sort(key=lambda x:(x.get("taste_score",0),math.log10((x.get("user_rating_count") or 0)+1),x.get("query_hits",1)),reverse=True)
        return out,{"candidate_count":meta.get("candidate_count",0),"verified_count":len(out),"api_calls":meta.get("api_calls",0)}
