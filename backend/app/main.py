from __future__ import annotations
import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from .db import Database
from .seed import REGIONAL_FOODS
from .services import RefreshService
from .settings import get_api_key_status, save_api_keys, clear_api_key, migrate_api_keys_from_sibling_versions
from .diagnostics import run_source_diagnostics, run_single_source_diagnostic
from .paths import get_app_data_dir, get_database_path, get_config_env_path, ensure_writable_dir, migrate_legacy_db

ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env", override=False)

APP_DATA_DIR = get_app_data_dir()
ensure_writable_dir(APP_DATA_DIR)

CONFIG_ENV_PATH = get_config_env_path()
KEY_MIGRATION = migrate_api_keys_from_sibling_versions(ROOT, CONFIG_ENV_PATH)
load_dotenv(CONFIG_ENV_PATH, override=True)

FRONTEND = ROOT / "frontend"
DATA_DIR = ROOT / "data"
PUBLIC_MODE = os.getenv("KFM_PUBLIC_MODE", "0").strip().lower() in {"1","true","yes","on"}

DB_PATH = get_database_path()
LEGACY_DB_MIGRATED_FROM = migrate_legacy_db(ROOT, DB_PATH)

db = Database(DB_PATH)
refresh_service = RefreshService(db)

def seed_foods_for_region(province: str, city: str):
    return [
        {
            "name":x["name"],
            "subtitle":x.get("subtitle"),
            "emoji":x.get("emoji"),
            "source_label":x.get("source_label"),
            "source_url":x.get("source_url"),
        }
        for x in REGIONAL_FOODS
        if x.get("province")==province and x.get("city")==city
    ]

@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init_schema()
    db.seed_foods(REGIONAL_FOODS)
    print(f"[DB] path={db.path}")
    if db.last_repair_reason:
        print(f"[DB] repaired={db.last_repair_reason}")
    if db.last_backup_path:
        print(f"[DB] backup={db.last_backup_path}")
    yield

app = FastAPI(title="Korea Food Map API", version="1.5.0", lifespan=lifespan)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    print(f"[ERROR] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "INTERNAL_SERVER_ERROR",
            "detail": f"{type(exc).__name__}: {str(exc)}",
            "path": str(request.url.path),
        },
    )

class RefreshRequest(BaseModel):
    province: str
    city: str
    bbox: list[float] | None = None

class ApiKeySettingsRequest(BaseModel):
    kakao: str | None = None
    data_go: str | None = None
    tourapi: str | None = None

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
        raise HTTPException(status_code=403, detail="공개 서비스에서는 API 키 설정을 변경할 수 없어.")

@app.get("/api/v1/settings/api-keys")
def api_key_settings():
    if PUBLIC_MODE:
        return {"ok": True, "public_mode": True, "keys": {"kakao":{"configured":bool(os.getenv("KAKAO_REST_API_KEY"))},"data_go":{"configured":bool(os.getenv("DATA_GO_KR_SERVICE_KEY"))},"tourapi":{"configured":bool(os.getenv("TOUR_API_SERVICE_KEY"))}}}
    return {"ok": True, "public_mode": False, "keys": get_api_key_status(CONFIG_ENV_PATH)}

@app.post("/api/v1/settings/api-keys")
def save_api_key_settings(req: ApiKeySettingsRequest):
    require_local_settings()
    keys = save_api_keys(
        CONFIG_ENV_PATH,
        kakao=req.kakao,
        data_go=req.data_go,
        tourapi=req.tourapi,
    )
    return {"ok": True,"message": "API 키를 저장했고 현재 서버 프로세스에도 즉시 반영했어.","keys": keys}

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
    return await run_source_diagnostics(kakao_key=req.kakao,data_go_key=req.data_go,tourapi_key=req.tourapi)

@app.get("/api/v1/health")
def health():
    return {"ok": True,"version": "1.5.0","db": str(db.path),"app_data_dir": str(APP_DATA_DIR),"config_env": str(CONFIG_ENV_PATH),"db_repair_reason": db.last_repair_reason,"db_backup_path": db.last_backup_path,"legacy_migrated_from": LEGACY_DB_MIGRATED_FROM,"key_migration": KEY_MIGRATION,"public_mode": PUBLIC_MODE}

@app.get("/api/v1/sources/status")
def sources_status():
    public_key = bool(os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip())
    return {"kakao": bool(os.getenv("KAKAO_REST_API_KEY", "").strip()),"data_go": public_key,"excellent": public_key,"tourapi": bool(os.getenv("TOUR_API_SERVICE_KEY", "").strip())}

@app.get("/api/v1/sources/test")
async def sources_test():
    return await run_source_diagnostics()

@app.get("/api/v1/stats")
def stats(province: str | None = None, city: str | None = None):
    try:
        result=db.get_stats(province=province,city=city)
        result["storage"]="sqlite"
        return result
    except Exception as exc:
        food_count=sum(1 for x in REGIONAL_FOODS if (not province or x.get("province")==province) and (not city or x.get("city")==city))
        return {"restaurants":refresh_service.memory_stats(province,city),"representative_foods":food_count,"markets":0,"markets_ready":False,"storage":"memory","storage_error":f"{type(exc).__name__}: {exc}"}

@app.post("/api/v1/db/repair")
def db_repair():
    if PUBLIC_MODE:
        raise HTTPException(status_code=403, detail="공개 서비스에서는 DB 복구 버튼을 사용할 수 없어.")
    from .paths import quarantine_bad_db
    backup=None
    if Path(db.path).exists():
        backup=quarantine_bad_db(Path(db.path))
    db.last_repair_reason="manual repair"
    db.last_backup_path=str(backup) if backup else None
    db.init_schema()
    db.seed_foods(REGIONAL_FOODS)
    return {"ok": True,"path": str(db.path),"backup_path": db.last_backup_path,"stats": db.get_stats()}

@app.get("/api/v1/db/diagnostics")
def db_diagnostics():
    current=Path(db.path)
    info={"ok":True,"path":str(current),"parent":str(current.parent),"exists":current.exists(),"parent_exists":current.parent.exists(),"parent_writable":os.access(current.parent, os.W_OK) if current.parent.exists() else False,"repair_reason":db.last_repair_reason,"backup_path":db.last_backup_path,"tables":[],"stats":None}
    try:
        with db.connect() as con:
            info["path"]=str(db.path)
            info["exists"]=Path(db.path).exists()
            info["tables"]=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        db.init_schema()
        info["stats"]=db.get_stats()
    except Exception as exc:
        info["ok"]=False
        info["error"]=f"{type(exc).__name__}: {exc}"
    return info

@app.get("/api/v1/region")
def region(province: str, city: str, limit: int = Query(500, ge=1, le=2000)):
    foods=seed_foods_for_region(province,city)
    storage="sqlite"
    storage_error=None
    try:
        db_foods=db.get_foods(province,city)
        if db_foods:
            foods=db_foods
        restaurants=db.get_restaurants(province,city,limit)
    except Exception as exc:
        storage="memory"
        storage_error=f"{type(exc).__name__}: {exc}"
        restaurants=refresh_service.get_cached_restaurants(province,city)[:limit]
    cached=refresh_service.get_cached_restaurants(province,city)
    if cached:
        restaurants=cached[:limit]
        if storage=="sqlite":
            storage="sqlite+memory"
    try:
        last_refresh=db.get_last_refresh(province,city)
    except Exception:
        last_refresh=refresh_service.get_cached_meta(province,city)
    return {"province":province,"city":city,"foods":foods,"restaurants":restaurants,"last_refresh":last_refresh,"storage":storage,"storage_error":storage_error}

@app.post("/api/v1/region/refresh")
async def refresh(req: RefreshRequest):
    if not req.province.strip() or not req.city.strip():
        raise HTTPException(400, "province/city required")
    result=await refresh_service.refresh(req.province.strip(),req.city.strip(),bbox=req.bbox)
    result["foods"]=seed_foods_for_region(req.province.strip(),req.city.strip())
    return result

@app.get("/api/v1/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(200, ge=1, le=1000)):
    return {"query": q, "restaurants": db.search_restaurants(q, limit)}

@app.get("/")
def home():
    return FileResponse(FRONTEND / "index.html")

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
