import hashlib, json, os, httpx
from urllib.parse import unquote
from ..diagnostics import _extract_public_error, normalize_public_key

GENERAL_URL="https://apis.data.go.kr/1741000/general_restaurants/info"
EXCELLENT_URL="https://apis.data.go.kr/1741000/excellent_restaurant_info/info"

def records(node):
    found=[]
    def walk(x):
        if isinstance(x,list):
            for v in x: walk(v)
        elif isinstance(x,dict):
            if set(x) & {"BPLC_NM","BSNSSP_NM","ROAD_NM_ADDR","SITE_WHL_ADDR","PRINC_FD_KND"}:
                found.append(x)
            else:
                for v in x.values(): walk(v)
    walk(node)
    return found

def pick(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,""): return str(v)
    return None

def pid(provider,d):
    raw=pick(d,"MNG_NO","BSNSSP_NM","BPLC_NM","ROAD_NM_ADDR") or json.dumps(d,sort_keys=True,ensure_ascii=False)
    return hashlib.sha1((provider+"|"+raw).encode()).hexdigest()

class PublicDataCollector:
    def __init__(self):
        self.key=normalize_public_key(os.getenv("DATA_GO_KR_SERVICE_KEY",""))
    @property
    def enabled(self): return bool(self.key)

    async def call(self,url,params):
        if not self.enabled:
            return []
        p={"serviceKey":self.key,"pageNo":1,"numOfRows":100,"returnType":"json",**params}
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r=await client.get(url,params=p)
            text=r.text[:8000]
            code,msg=_extract_public_error(text)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {msg or text[:300]}")
            if code:
                raise RuntimeError(f"{code}: {msg}")
            try:
                payload=r.json()
            except Exception as e:
                raise RuntimeError(f"공공데이터 API 비JSON 응답: {text[:300]}") from e
        return records(payload)

    async def excellent(self,province,city):
        rows=await self.call(EXCELLENT_URL,{"cond[ROAD_NM_ADDR::LIKE]":city})
        out=[]
        for d in rows:
            out.append({
              "provider":"excellent","provider_id":pid("excellent",d),
              "province":province,"city":city,
              "name":pick(d,"BSNSSP_NM","BPLC_NM","BIZPLC_NM") or "이름없음",
              "category":pick(d,"PRINC_FD_KND","UPTAE_NM","FD_KND"),
              "business_type":"모범음식점",
              "address":pick(d,"SITE_WHL_ADDR","SITE_ADDR"),
              "road_address":pick(d,"ROAD_NM_ADDR","RDNMADR"),
              "phone":pick(d,"TELNO","TEL","PHONE"),
              "x":pick(d,"X","X_CRDNT"),"y":pick(d,"Y","Y_CRDNT"),
              "place_url":None,
              "status":pick(d,"SALS_STTS_NM","DTL_SALS_STTS_NM","DSGN_STTS_NM") or "지정",
              "verified_public":True,"raw_json":d
            })
        return out

    async def verify_by_name(self,city,name):
        return await self.call(GENERAL_URL,{
          "cond[BPLC_NM::LIKE]":name,
          "cond[ROAD_NM_ADDR::LIKE]":city
        })
