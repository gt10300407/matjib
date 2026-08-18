from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Dict

ENV_KEYS = {
    "kakao": "KAKAO_REST_API_KEY",
    "data_go": "DATA_GO_KR_SERVICE_KEY",
    "tourapi": "TOUR_API_SERVICE_KEY",
}

def mask_key(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]}"

def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        values[key.strip()] = value.strip()
    return values

def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(ch in value for ch in [' ', '#', '"', "'"]):
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{value}"'
    return value

def write_env_file(path: Path, updates: Dict[str, str]) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    handled = set()
    output = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={_quote_env_value(updates[key])}")
            handled.add(key)
        else:
            output.append(line)

    for key, value in updates.items():
        if key not in handled:
            output.append(f"{key}={_quote_env_value(value)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")

    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

def get_api_key_status(env_path: Path):
    file_values = read_env_file(env_path)
    result = {}
    for logical_name, env_name in ENV_KEYS.items():
        value = os.getenv(env_name, "").strip() or file_values.get(env_name, "").strip()
        result[logical_name] = {
            "configured": bool(value),
            "masked": mask_key(value),
        }
    return result

def save_api_keys(env_path: Path, kakao: str | None, data_go: str | None, tourapi: str | None):
    supplied = {
        "KAKAO_REST_API_KEY": kakao,
        "DATA_GO_KR_SERVICE_KEY": data_go,
        "TOUR_API_SERVICE_KEY": tourapi,
    }

    updates = {
        env_name: value.strip()
        for env_name, value in supplied.items()
        if value is not None and value.strip()
    }

    if updates:
        write_env_file(env_path, updates)
        for env_name, value in updates.items():
            os.environ[env_name] = value

    return get_api_key_status(env_path)

def clear_api_key(env_path: Path, logical_name: str):
    if logical_name not in ENV_KEYS:
        raise KeyError(logical_name)
    env_name = ENV_KEYS[logical_name]
    write_env_file(env_path, {env_name: ""})
    os.environ.pop(env_name, None)
    return get_api_key_status(env_path)


def migrate_api_keys_from_sibling_versions(project_root: Path, persistent_env: Path):
    persistent = read_env_file(persistent_env)
    missing = [
        env_name for env_name in ENV_KEYS.values()
        if not persistent.get(env_name, "").strip()
    ]
    if not missing:
        return {"migrated": False, "from": None, "keys": []}

    parent = project_root.parent
    candidates = []
    try:
        for p in parent.glob("korea_food_map_v*"):
            if p.resolve() == project_root.resolve():
                continue
            env = p / ".env"
            if env.exists() and env.is_file():
                candidates.append(env)
    except Exception:
        return {"migrated": False, "from": None, "keys": []}

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    updates = {}
    source = None
    for env in candidates:
        vals = read_env_file(env)
        for key in list(missing):
            value = vals.get(key, "").strip()
            if value:
                updates[key] = value
                missing.remove(key)
                source = source or str(env)
        if not missing:
            break

    if updates:
        write_env_file(persistent_env, updates)
        for key, value in updates.items():
            os.environ[key] = value
        return {"migrated": True, "from": source, "keys": sorted(updates)}

    return {"migrated": False, "from": None, "keys": []}
