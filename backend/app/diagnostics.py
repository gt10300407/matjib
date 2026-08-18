from __future__ import annotations

import hashlib
import json
import os
import re
import time
from urllib.parse import unquote

import httpx

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
GENERAL_URL = "https://apis.data.go.kr/1741000/general_restaurants/info"
EXCELLENT_URL = "https://apis.data.go.kr/1741000/excellent_restaurant_info/info"
TOUR_URL = "https://apis.data.go.kr/B551011/KorService2/areaCode2"

KNOWN_PUBLIC_ERRORS = {
    "APPLICATION_ERROR": "공공데이터 게이트웨이 내부 오류가 발생했어.",
    "HTTP_ERROR": "허용되지 않은 요청이거나 기관 API 응답 처리에 실패했어.",
    "SERVICETIMEOUT_ERROR": "기관 API 응답 시간이 초과됐어.",
    "INVALID_REQUEST_PARAMETER_ERROR": "요청 파라미터 값/형식이 올바르지 않아.",
    "NO_OPENAPI_SERVICE_ERROR": "요청한 OpenAPI 서비스가 없거나 URL이 잘못됐어.",
    "SERVICE_KEY_IS_NULL": "인증키가 요청에 포함되지 않았어.",
    "PERMISSION_DENIED": "API 접근 권한이 거부됐어. 해당 API 활용신청을 확인해.",
    "SERVICE_ACCESS_DENIED_ERROR": "해당 API 이용 권한이 없어. 이 API를 별도로 활용신청했는지 확인해.",
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "등록되지 않은 인증키야. 키 또는 활용신청 상태를 확인해.",
    "DEADLINE_HAS_EXPIRED_ERROR": "API 인증키 사용기간이 만료됐어.",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "일일 호출 한도를 초과했어.",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR": "초당 호출 한도를 초과했어.",
    "BLACKLIST_IP_ACCESS_ERROR": "현재 호출 IP가 차단됐어.",
}

def clean_secret(value: str | None) -> str:
    value = (value or "").strip()
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        value = value[1:-1].strip()
    if value.lower().startswith("servicekey="):
        value = value.split("=", 1)[1].strip()
    if value.startswith("KakaoAK "):
        value = value[len("KakaoAK "):].strip()
    return value

def normalize_public_key(value: str | None) -> str:
    value = clean_secret(value)
    try:
        return unquote(value)
    except Exception:
        return value

def mask_key(value: str | None) -> str:
    value = clean_secret(value)
    if not value:
        return "없음"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]} ({len(value)}자)"

def fingerprint(value: str | None) -> str:
    value = clean_secret(value)
    if not value:
        return "-"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]

def _extract_public_error(text: str):
    upper = text.upper()
    for code, msg in KNOWN_PUBLIC_ERRORS.items():
        if code in upper:
            return code, msg
    try:
        data = json.loads(text)
        candidates = [data]
        if isinstance(data, dict):
            if isinstance(data.get("response"), dict):
                candidates.append(data["response"])
                if isinstance(data["response"].get("header"), dict):
                    candidates.append(data["response"]["header"])
            if isinstance(data.get("header"), dict):
                candidates.append(data["header"])
        for node in candidates:
            if not isinstance(node, dict):
                continue
            code = node.get("resultCode") or node.get("returnReasonCode")
            msg = node.get("resultMsg") or node.get("returnAuthMsg")
            if code is not None and str(code) not in {"00", "0", "0000"}:
                return str(code), str(msg or "공공데이터 API 오류")
    except Exception:
        pass
    for code_tag, msg_tag in [("resultCode", "resultMsg"),("returnReasonCode", "returnAuthMsg")]:
        mc = re.search(fr"<{code_tag}>\s*([^<]+)\s*</{code_tag}>", text, re.I)
        mm = re.search(fr"<{msg_tag}>\s*([^<]+)\s*</{msg_tag}>", text, re.I)
        if mc and mc.group(1).strip() not in {"00", "0", "0000"}:
            code = mc.group(1).strip()
            msg = mm.group(1).strip() if mm else "공공데이터 API 오류"
            return code, KNOWN_PUBLIC_ERRORS.get(msg.upper(), msg)
    return None, None

def _base_result(name: str, configured: bool, key: str, endpoint: str, auth: str):
    return {"name": name,"configured": configured,"key_hint": mask_key(key),"key_fingerprint": fingerprint(key),"ok": False,"http_status": None,"content_type": None,"latency_ms": None,"endpoint": endpoint,"auth": auth,"message": "키가 설정되지 않았어." if not configured else "테스트 전","sample": None}

async def test_kakao(client: httpx.AsyncClient, key_override: str | None = None):
    key = clean_secret(key_override if key_override is not None else os.getenv("KAKAO_REST_API_KEY", ""))
    result = _base_result("Kakao Local",bool(key),key,KAKAO_URL,"Authorization: KakaoAK {REST_API_KEY}")
    if not key:
        return result
    started = time.perf_counter()
    try:
        r = await client.get(KAKAO_URL,headers={"Authorization": f"KakaoAK {key}"},params={"query": "안산시", "page": 1, "size": 1})
        result["http_status"] = r.status_code
        result["content_type"] = r.headers.get("content-type")
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        body_text = r.text[:2000]
        if r.status_code == 200:
            data = r.json()
            result["ok"] = True
            result["message"] = "입력한 REST API 키로 Kakao Local 실제 호출 성공"
            docs = data.get("documents", [])
            if docs:
                d = docs[0]
                result["sample"] = {"place_name": d.get("place_name"),"category": d.get("category_name"),"address": d.get("road_address_name") or d.get("address_name")}
            return result
        try:
            body = r.json()
            msg = body.get("msg") or body.get("message") or body.get("error") or json.dumps(body, ensure_ascii=False)
            code = body.get("code")
        except Exception:
            msg = body_text
            code = None
        if r.status_code == 401:
            result["message"] = f"401 인증 실패: REST API 키 종류/값을 확인해. {msg}"
        elif r.status_code == 403:
            result["message"] = "403 권한 실패: 키 값 자체가 맞더라도 Kakao Developers 앱에서 Kakao Map API 사용 설정이 ON인지 확인해. " + str(msg)
        elif r.status_code == 429:
            result["message"] = f"429 호출 한도 초과: {msg}"
        else:
            result["message"] = f"HTTP {r.status_code}: {msg}"
        if code is not None:
            result["sample"] = {"kakao_error_code": code}
    except Exception as e:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        result["message"] = f"네트워크/호출 오류: {type(e).__name__}: {e}"
    return result

async def _test_public(client: httpx.AsyncClient,name: str,url: str,key_override: str | None = None):
    raw_key = clean_secret(key_override if key_override is not None else os.getenv("DATA_GO_KR_SERVICE_KEY", ""))
    key = normalize_public_key(raw_key)
    result = _base_result(name,bool(raw_key),raw_key,url,"query parameter: serviceKey")
    if not raw_key:
        return result
    params = {"serviceKey": key,"pageNo": 1,"numOfRows": 1,"returnType": "json"}
    started = time.perf_counter()
    try:
        r = await client.get(url, params=params)
        result["http_status"] = r.status_code
        result["content_type"] = r.headers.get("content-type")
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        text = r.text[:5000]
        code, known_msg = _extract_public_error(text)
        if r.status_code != 200:
            result["message"] = f"HTTP {r.status_code}: {known_msg or text[:300]}"
            return result
        if code:
            result["message"] = f"{code}: {known_msg}"
            return result
        result["ok"] = True
        result["message"] = "입력한 공공데이터 인증키로 실제 호출 성공"
        try:
            data = r.json()
            result["sample"] = {"response_type": "json","top_keys": list(data.keys())[:6] if isinstance(data, dict) else []}
        except Exception:
            result["sample"] = {"response_type": "xml/text","preview": re.sub(r"\s+", " ", text[:160])}
    except Exception as e:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        result["message"] = f"네트워크/호출 오류: {type(e).__name__}: {e}"
    return result

async def test_tourapi(client: httpx.AsyncClient, key_override: str | None = None):
    raw_key = clean_secret(key_override if key_override is not None else os.getenv("TOUR_API_SERVICE_KEY", ""))
    key = normalize_public_key(raw_key)
    result = _base_result("TourAPI",bool(raw_key),raw_key,TOUR_URL,"query parameter: serviceKey")
    if not raw_key:
        return result
    params = {"serviceKey": key,"numOfRows": 1,"pageNo": 1,"MobileOS": "ETC","MobileApp": "KoreaFoodMap","_type": "json"}
    started = time.perf_counter()
    try:
        r = await client.get(TOUR_URL, params=params)
        result["http_status"] = r.status_code
        result["content_type"] = r.headers.get("content-type")
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        text = r.text[:5000]
        code, known_msg = _extract_public_error(text)
        if r.status_code != 200:
            result["message"] = f"HTTP {r.status_code}: {known_msg or text[:300]}"
            return result
        if code:
            result["message"] = f"{code}: {known_msg}"
            return result
        try:
            data = r.json()
            header = (((data.get("response") or {}).get("header")) or {})
            result_code = str(header.get("resultCode", ""))
            result_msg = header.get("resultMsg")
            if result_code and result_code not in {"0000", "00", "0"}:
                result["message"] = f"TourAPI 오류 {result_code}: {result_msg}"
                return result
            result["ok"] = True
            result["message"] = "입력한 TourAPI 인증키로 실제 호출 성공"
            result["sample"] = {"resultCode": result_code or None,"resultMsg": result_msg}
        except Exception:
            result["message"] = "응답은 왔지만 정상 JSON이 아니야."
            result["sample"] = {"preview": re.sub(r"\s+", " ", text[:180])}
    except Exception as e:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        result["message"] = f"네트워크/호출 오류: {type(e).__name__}: {e}"
    return result

async def run_single_source_diagnostic(source: str, key_override: str | None = None):
    timeout = httpx.Timeout(15.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if source == "kakao": return await test_kakao(client, key_override)
        if source == "general": return await _test_public(client, "일반음식점 API", GENERAL_URL, key_override)
        if source == "excellent": return await _test_public(client, "모범음식점 API", EXCELLENT_URL, key_override)
        if source == "tourapi": return await test_tourapi(client, key_override)
    raise ValueError(f"unsupported source: {source}")

async def run_source_diagnostics(kakao_key: str | None = None,data_go_key: str | None = None,tourapi_key: str | None = None):
    timeout = httpx.Timeout(15.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        kakao = await test_kakao(client, kakao_key)
        general = await _test_public(client, "일반음식점 API", GENERAL_URL, data_go_key)
        excellent = await _test_public(client, "모범음식점 API", EXCELLENT_URL, data_go_key)
        tourapi = await test_tourapi(client, tourapi_key)
    items = {"kakao": kakao,"general": general,"excellent": excellent,"tourapi": tourapi}
    configured = sum(1 for x in items.values() if x["configured"])
    ok = sum(1 for x in items.values() if x["ok"])
    return {"ok": ok == len(items),"configured_count": configured,"success_count": ok,"total_count": len(items),"items": items}

_public_error_from_text = _extract_public_error
