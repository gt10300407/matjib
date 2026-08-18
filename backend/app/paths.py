from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

APP_DIR_NAME = ".korea_food_map"

def get_app_data_dir() -> Path:
    # Deliberately simple and stable across app versions.
    # Mac/Linux: ~/.korea_food_map
    # Windows:    %USERPROFILE%\.korea_food_map
    return Path.home() / APP_DIR_NAME

def ensure_writable_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)

def get_database_path() -> Path:
    app_dir = get_app_data_dir()
    ensure_writable_dir(app_dir)
    return app_dir / "korea_food.sqlite3"

def get_config_env_path() -> Path:
    app_dir = get_app_data_dir()
    ensure_writable_dir(app_dir)
    return app_dir / ".env"

def quarantine_bad_db(path: Path) -> Path | None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + f".broken-{stamp}")
    moved = None

    if path.exists():
        try:
            shutil.move(str(path), str(backup))
            moved = backup
        except Exception:
            moved = None

    # WAL/SHM/JOURNAL sidecars can themselves be the source of write failures.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            try:
                shutil.move(str(sidecar), str(backup) + suffix)
            except Exception:
                try:
                    sidecar.unlink(missing_ok=True)
                except Exception:
                    pass

    return moved

def migrate_legacy_db(project_root: Path, target: Path) -> str | None:
    if target.exists():
        return None

    candidates = [
        project_root / "data" / "korea_food.sqlite3",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                shutil.copy2(candidate, target)
                return str(candidate)
            except Exception:
                pass
    return None
