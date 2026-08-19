from __future__ import annotations

import asyncio
import os
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import Database
from .diagnostics import run_source_diagnostics, run_single_source_diagnostic
from .paths import get_app_data_dir, get_database_path, get_config_env_path, ensure_writable_dir, migrate_legacy_db
from .seed import REGIONAL_FOODS
from .services import RefreshService
from .settings import clear_api_key, get_api_key_status, migrate_api_keys_from_sibling_versions, save_api_keys

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)
APP_DATA_DIR = get_app_data_dir()
ensure_writable_dir(APP_DATA_DIR)
CONFIG_ENV_PATH = get_config_env_path()
KEY_MIGRATION = migrate_api_keys_from_sibling_versions(ROOT, CONFIG_ENV_PATH)
load_dotenv(CONFIG_ENV_PATH, override=True)
FRONTEND = ROOT / "frontend"
DATA_DIR = ROOT / "data"
PUBLIC_MODE = os.getenv("KFM_PUBLIC_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
DB_PATH = get_database_path()
LEGACY_DB_MIGRATED_FROM = migrate_legacy_db(ROOT, DB_PATH)
db = Database(DB_PATH)
refresh_service = RefreshService(db)
APP_VERSION = "4.8.3"

_refresh_tasks: dict[tuple[str, str], asyncio.Task] = {}
_refresh_jobs: dict[tuple[str, str], dict] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refresh_key(province: str, city: str) -> tuple[str, str]:
    return province.strip(), city.strip()


def _compact_refresh_result(result: dict) -> dict:
    keep = {
        "ok", "partial", "province", "city", "candidate_count", "recommended_count", "verified_count",
        "total_seen", "successful_sources", "failed_sources", "google_api_calls", "kakao_api_calls",
        "naver_api_calls", "public_api_calls", "public_master_count", "storage", "storage_error", "source_results",
        "definition", "criteria",
    }
    return {k: v for k, v in result.items() if k in keep}


def seed_foods_for_region(province: str, city: str):
    return [
        {
            "name": x["name"],
            "subtitle": x.get("subtitle"),
            "emoji": x.get("emoji"),
            "source_label": x.get("source_label"),
            "source_url": x.get("source_url"),
        }
        for x in REGIONAL_FOODS
        if x.get("province") == province and x.get("city") == city
    ]


async def _run_refresh_job(province: str, city: str, bbox: list[float] | None):
    key = _refresh_key(province, city)
    job = _refresh_jobs[key]
    job["status"] = "running"
    job["started_at"] = _utc_now()
    try:
        result = await refresh_service.refresh(province, city, bbox=bbox)
        job["status"] = "completed"
        job["finished_at"] = _utc_now()
        job["result"] = _compact_refresh_result(result)
        job["error"] = None
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["finished_at"] = _utc_now()
        raise
    except Exception as exc:
        job["status"] = "failed"
        job["finished_at"] = _utc_now()
        job["error"] = {"type": type(exc).__name__, "message": str(exc) or type(exc).__name__}
        print(f"[REFRESH] background failure {province}/{city}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        _refresh_tasks.pop(key, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init_schema()
    db.seed_foods(REGIONAL_FOODS)
    refresh_service.taste_store.init_schema()
    print(f"[DB] path={db.path}")
    yield
    tasks = list(_refresh_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Korea Food Map API", version=APP_VERSION, lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    print(f"[ERROR] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    detail = "서버 내부 오류가 발생했어." if PUBLIC_MODE else f"{type(exc).__name__}: {str(exc)}"
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "INTERNAL_SERVER_ERROR", "detail": detail, "path": str(request.url.path)},
    )


class RefreshRequest(BaseModel):
    province: str
    city: str
    bbox: list[float] | None = None


class LiveSearchRequest(BaseModel):
    q: str
    province: str
    city: str
    bbox: list[float] | None = None


class ApiKeySettingsRequest(BaseModel):
    kakao: str | None = None
    data_go: str | None = None
    tourapi: str | None = None
    google: str | None = None


class ApiKeyClearRequest(BaseModel):
    source: str


class ApiKeyTestRequest(BaseModel):
    source: str
    key: str | None = None


class ApiKeysTestAllRequest(BaseModel):
    kakao: str | None = None
    data_go: str | None = None
    tourapi: str | None = None


def require_local_settings():
    if PUBLIC_MODE:
        raise HTTPException(status_code=403, detail="공개 서비스에서는 관리자 진단/설정 기능을 사용할 수 없어.")


@app.get("/api/v1/settings/api-keys")
def api_key_settings():
    if PUBLIC_MODE:
        return {
            "ok": True,
            "public_mode": True,
            "keys": {
                "kakao": {"configured": bool(os.getenv("KAKAO_REST_API_KEY"))},
                "data_go": {"configured": bool(os.getenv("DATA_GO_KR_SERVICE_KEY"))},
                "tourapi": {"configured": bool(os.getenv("TOUR_API_SERVICE_KEY"))},
                "google": {"configured": bool(os.getenv("GOOGLE_PLACES_API_KEY"))},
            },
        }
    return {"ok": True, "public_mode": False, "keys": get_api_key_status(CONFIG_ENV_PATH)}


@app.post("/api/v1/settings/api-keys")
def save_api_key_settings(req: ApiKeySettingsRequest):
    require_local_settings()
    keys = save_api_keys(
        CONFIG_ENV_PATH,
        kakao=req.kakao,
        data_go=req.data_go,
        tourapi=req.tourapi,
        google=req.google,
    )
    return {"ok": True, "message": "API 키를 저장했고 현재 서버 프로세스에도 즉시 반영했어.", "keys": keys}


@app.post("/api/v1/settings/api-keys/clear")
def clear_api_key_setting(req: ApiKeyClearRequest):
    require_local_settings()
    try:
        keys = clear_api_key(CONFIG_ENV_PATH, req.source)
    except KeyError:
        raise HTTPException(status_code=400, detail="지원하지 않는 API source야.")
    return {"ok": True, "keys": keys}


@app.get("/api/v1/settings/api-keys/test/{source}")
async def test_single_api_key(source: str):
    require_local_settings()
    if source not in {"kakao", "general", "excellent", "tourapi"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 API source야.")
    return await run_single_source_diagnostic(source)


@app.post("/api/v1/settings/api-keys/test")
async def test_api_key_direct(req: ApiKeyTestRequest):
    require_local_settings()
    if req.source not in {"kakao", "general", "excellent", "tourapi"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 API source야.")
    return await run_single_source_diagnostic(req.source, key_override=req.key)


@app.post("/api/v1/settings/api-keys/test-all")
async def test_all_api_keys_direct(req: ApiKeysTestAllRequest):
    require_local_settings()
    return await run_source_diagnostics(kakao_key=req.kakao, data_go_key=req.data_go, tourapi_key=req.tourapi)


@app.get("/api/v1/health")
def health():
    if PUBLIC_MODE:
        return {"ok": True, "version": APP_VERSION, "public_mode": True}
    return {
        "ok": True,
        "version": APP_VERSION,
        "db": str(db.path),
        "app_data_dir": str(APP_DATA_DIR),
        "config_env": str(CONFIG_ENV_PATH),
        "legacy_migrated_from": str(LEGACY_DB_MIGRATED_FROM) if LEGACY_DB_MIGRATED_FROM else None,
        "key_migration": KEY_MIGRATION,
        "public_mode": False,
    }


@app.get("/api/v1/sources/status")
def sources_status():
    public_key = bool(os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip())
    return {
        "google": bool(os.getenv("GOOGLE_PLACES_API_KEY", "").strip()),
        "kakao": bool(os.getenv("KAKAO_REST_API_KEY", "").strip()),
        "data_go": public_key,
        "excellent": public_key,
        "tourapi": bool(os.getenv("TOUR_API_SERVICE_KEY", "").strip()),
    }


@app.get("/api/v1/sources/test")
async def sources_test():
    require_local_settings()
    return await run_source_diagnostics()


@app.get("/api/v1/stats")
def stats(province: str | None = None, city: str | None = None):
    food_count = sum(
        1
        for x in REGIONAL_FOODS
        if (not province or x.get("province") == province) and (not city or x.get("city") == city)
    )
    public_inventory = 0
    if province and city:
        try:
            public_inventory = refresh_service.taste_store.public_inventory_count(province, city)
        except Exception:
            public_inventory = 0
    return {
        "restaurants": refresh_service.count_verified(province, city),
        "public_inventory": public_inventory,
        "representative_foods": food_count,
        "markets": 0,
        "markets_ready": False,
        "definition": "local_intent_top10",
    }


@app.post("/api/v1/db/repair")
def db_repair():
    require_local_settings()
    from .paths import quarantine_bad_db

    backup = quarantine_bad_db(Path(db.path)) if Path(db.path).exists() else None
    db.last_repair_reason = "manual repair"
    db.last_backup_path = str(backup) if backup else None
    db.init_schema()
    db.seed_foods(REGIONAL_FOODS)
    refresh_service.taste_store.init_schema()
    return {"ok": True, "path": str(db.path), "backup_path": db.last_backup_path, "stats": stats()}


@app.get("/api/v1/db/diagnostics")
def db_diagnostics():
    require_local_settings()
    current = Path(db.path)
    return {"ok": True, "path": str(current), "exists": current.exists(), "verified_count": refresh_service.count_verified()}


@app.get("/api/v1/region")
def region(province: str, city: str, limit: int = Query(300, ge=1, le=1000)):
    foods = seed_foods_for_region(province, city)
    restaurants = refresh_service.get_cached_restaurants(province, city, limit)
    key = _refresh_key(province, city)
    job = _refresh_jobs.get(key)
    refresh_status = job.get("status") if job else "idle"
    return {
        "ok": True,
        "province": province,
        "city": city,
        "foods": foods,
        "restaurants": restaurants,
        "verified_count": len(restaurants),
        "definition": "local_intent_top10",
        "last_refresh": refresh_service.get_cached_meta(province, city),
        "refresh_status": refresh_status,
    }


@app.post("/api/v1/region/refresh")
async def refresh(req: RefreshRequest):
    if not req.province.strip() or not req.city.strip():
        raise HTTPException(400, "province/city required")
    result = await refresh_service.refresh(req.province.strip(), req.city.strip(), bbox=req.bbox)
    result["foods"] = seed_foods_for_region(req.province.strip(), req.city.strip())
    return result


@app.post("/api/v1/region/refresh-async", status_code=202)
async def refresh_async(req: RefreshRequest):
    province, city = req.province.strip(), req.city.strip()
    if not province or not city:
        raise HTTPException(400, "province/city required")

    key = _refresh_key(province, city)
    existing = _refresh_tasks.get(key)
    cached_count = len(refresh_service.get_cached_restaurants(province, city, 1000))
    if existing and not existing.done():
        job = _refresh_jobs.get(key, {})
        return {
            "ok": True,
            "accepted": True,
            "already_running": True,
            "province": province,
            "city": city,
            "status": job.get("status", "running"),
            "queued_at": job.get("queued_at"),
            "started_at": job.get("started_at"),
            "cached_count": cached_count,
            "message": "이미 최신화 중이야. 기존 저장 데이터를 계속 볼 수 있어.",
        }

    _refresh_jobs[key] = {
        "status": "queued",
        "province": province,
        "city": city,
        "queued_at": _utc_now(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    task = asyncio.create_task(_run_refresh_job(province, city, req.bbox), name=f"refresh:{province}:{city}")
    _refresh_tasks[key] = task
    return {
        "ok": True,
        "accepted": True,
        "already_running": False,
        "province": province,
        "city": city,
        "status": "queued",
        "queued_at": _refresh_jobs[key]["queued_at"],
        "cached_count": cached_count,
        "message": "최신화를 서버에서 시작했어. 화면을 닫거나 다른 지역을 봐도 돼.",
    }


@app.get("/api/v1/region/refresh-status")
def refresh_status(province: str, city: str):
    province, city = province.strip(), city.strip()
    if not province or not city:
        raise HTTPException(400, "province/city required")
    key = _refresh_key(province, city)
    job = _refresh_jobs.get(key)
    cached_count = len(refresh_service.get_cached_restaurants(province, city, 1000))
    if not job:
        return {"ok": True, "province": province, "city": city, "status": "idle", "cached_count": cached_count}
    return {"ok": True, **job, "cached_count": cached_count}


@app.get("/api/v1/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(100, ge=1, le=500)):
    return {"query": q, "restaurants": refresh_service.search_verified(q, limit)}


@app.post("/api/v1/search/live")
async def live_search(req: LiveSearchRequest):
    if not req.q.strip():
        raise HTTPException(400, "q required")
    if not req.province.strip() or not req.city.strip():
        raise HTTPException(400, "province/city required")
    return await refresh_service.live_search(req.province.strip(), req.city.strip(), req.q.strip(), bbox=req.bbox)


@app.get("/")
def home():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
