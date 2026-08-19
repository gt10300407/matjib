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
APP_VERSION = "4.8.2"

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


async def _run_refresh_job(key: tuple[str, str], province: str, city: str, bbox):
    job = _refresh_jobs[key]
    job["status"] = "running"
    job["started_at"] = _utc_now()
    try:
        result = await refresh_service.refresh(province, city, bbox)
        job["status"] = "completed"
        job["completed_at"] = _utc_now()
        job["result"] = _compact_refresh_result(result)
        job["error"] = None
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["completed_at"] = _utc_now()
        raise
    except Exception as exc:
        job["status"] = "failed"
        job["completed_at"] = _utc_now()
        job["error"] = {"type": type(exc).__name__, "message": str(exc) or type(exc).__name__}
        job["traceback"] = traceback.format_exc(limit=8)
    finally:
        _refresh_tasks.pop(key, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for task in list(_refresh_tasks.values()):
        task.cancel()


app = FastAPI(title="Korea Food Map", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


class RefreshRequest(BaseModel):
    province: str
    city: str
    bbox: list[float] | None = None


class LiveSearchRequest(BaseModel):
    q: str
    province: str
    city: str
    bbox: list[float] | None = None


class ApiKeysPayload(BaseModel):
    kakao: str | None = None
    data_go: str | None = None
    tourapi: str | None = None
    google: str | None = None


class ClearApiKeyPayload(BaseModel):
    source: str


class ApiKeyTestPayload(BaseModel):
    source: str
    key: str | None = None


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/v1/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "public_mode": PUBLIC_MODE,
        "db_path": str(DB_PATH),
        "legacy_db_migrated_from": str(LEGACY_DB_MIGRATED_FROM) if LEGACY_DB_MIGRATED_FROM else None,
        "key_migration": KEY_MIGRATION,
    }


@app.get("/api/v1/region")
def region(province: str = Query(...), city: str = Query(...), limit: int = Query(300, ge=1, le=1000)):
    restaurants = refresh_service.get_cached_restaurants(province, city, limit)
    return {
        "ok": True,
        "province": province,
        "city": city,
        "restaurants": restaurants,
        "verified_count": len(restaurants),
        "foods": REGIONAL_FOODS.get(city, []),
    }


@app.post("/api/v1/region/refresh")
async def refresh_region(req: RefreshRequest):
    result = await refresh_service.refresh(req.province, req.city, req.bbox)
    result["foods"] = REGIONAL_FOODS.get(req.city, [])
    return result


@app.post("/api/v1/region/refresh-async")
async def refresh_region_async(req: RefreshRequest):
    key = _refresh_key(req.province, req.city)
    running = _refresh_tasks.get(key)
    if running and not running.done():
        job = _refresh_jobs.get(key, {})
        return {
            "ok": True,
            "accepted": True,
            "already_running": True,
            "status": job.get("status", "running"),
            "province": req.province,
            "city": req.city,
            "cached_count": len(refresh_service.get_cached_restaurants(req.province, req.city, 300)),
            "started_at": job.get("started_at") or job.get("queued_at"),
        }

    job = {
        "status": "queued",
        "province": req.province,
        "city": req.city,
        "queued_at": _utc_now(),
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error": None,
    }
    _refresh_jobs[key] = job
    task = asyncio.create_task(_run_refresh_job(key, req.province, req.city, req.bbox))
    _refresh_tasks[key] = task
    return {
        "ok": True,
        "accepted": True,
        "already_running": False,
        "status": "queued",
        "province": req.province,
        "city": req.city,
        "cached_count": len(refresh_service.get_cached_restaurants(req.province, req.city, 300)),
        "queued_at": job["queued_at"],
    }


@app.get("/api/v1/region/refresh-status")
def refresh_region_status(province: str = Query(...), city: str = Query(...)):
    key = _refresh_key(province, city)
    job = dict(_refresh_jobs.get(key) or {})
    if not job:
        return {
            "ok": True,
            "status": "idle",
            "province": province,
            "city": city,
            "cached_count": len(refresh_service.get_cached_restaurants(province, city, 300)),
        }
    job["ok"] = True
    job["cached_count"] = len(refresh_service.get_cached_restaurants(province, city, 300))
    return job


@app.post("/api/v1/search/live")
async def live_search(req: LiveSearchRequest):
    return await refresh_service.live_search(req.province, req.city, req.q, req.bbox)


@app.get("/api/v1/stats")
def stats(province: str | None = None, city: str | None = None):
    return {
        "ok": True,
        "restaurants": refresh_service.count_verified(province, city),
        "representative_foods": len(REGIONAL_FOODS.get(city or "", [])) if city else 0,
        "markets_ready": False,
    }


@app.get("/api/v1/sources/status")
def sources_status():
    status = get_api_key_status()
    return {"ok": True, **{k: bool(v.get("configured")) for k, v in status.items()}}


@app.get("/api/v1/settings/api-keys")
def api_keys_status():
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True, "keys": get_api_key_status()}


@app.post("/api/v1/settings/api-keys")
def api_keys_save(payload: ApiKeysPayload):
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    save_api_keys(payload.model_dump(exclude_none=True))
    return {"ok": True, "keys": get_api_key_status()}


@app.post("/api/v1/settings/api-keys/clear")
def api_key_clear(payload: ClearApiKeyPayload):
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    clear_api_key(payload.source)
    return {"ok": True, "keys": get_api_key_status()}


@app.post("/api/v1/settings/api-keys/test")
async def api_key_test(payload: ApiKeyTestPayload):
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return await run_single_source_diagnostic(payload.source, payload.key)


@app.post("/api/v1/settings/api-keys/test-all")
async def api_key_test_all(payload: ApiKeysPayload):
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return await run_source_diagnostics(payload.model_dump(exclude_none=True))


@app.get("/api/v1/db/diagnostics")
def db_diagnostics():
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return db.diagnostics()


@app.post("/api/v1/db/repair")
def db_repair():
    if PUBLIC_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return db.repair()


@app.exception_handler(Exception)
async def unhandled_error(_request, exc: Exception):
    return JSONResponse(status_code=500, content={"ok": False, "error": type(exc).__name__, "detail": str(exc)})
