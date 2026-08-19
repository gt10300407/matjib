from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class TasteStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.init_schema()

    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_path), timeout=15.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=15000")
        return con

    @staticmethod
    def _ensure_column(con, table: str, column: str, declaration: str):
        names = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in names:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def init_schema(self):
        with self.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS verified_restaurants(
                  provider_id TEXT PRIMARY KEY, province TEXT NOT NULL, city TEXT NOT NULL, name TEXT NOT NULL,
                  cuisine TEXT, primary_type TEXT, address TEXT, phone TEXT, x TEXT, y TEXT, place_url TEXT,
                  rating REAL NOT NULL, user_rating_count INTEGER NOT NULL, taste_score REAL NOT NULL,
                  query_hits INTEGER NOT NULL DEFAULT 1, raw_json TEXT, updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(con, "verified_restaurants", "phone", "TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_verified_region ON verified_restaurants(province,city)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_verified_cuisine ON verified_restaurants(cuisine)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_verified_score ON verified_restaurants(taste_score DESC)")

            con.execute(
                """
                CREATE TABLE IF NOT EXISTS public_restaurant_inventory(
                  provider_id TEXT PRIMARY KEY, province TEXT NOT NULL, city TEXT NOT NULL,
                  name TEXT NOT NULL, category TEXT, address TEXT, phone TEXT, status TEXT,
                  source_provider TEXT, business_type TEXT,
                  raw_json TEXT, fetched_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(con, "public_restaurant_inventory", "source_provider", "TEXT")
            self._ensure_column(con, "public_restaurant_inventory", "business_type", "TEXT")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_inventory_region ON public_restaurant_inventory(province,city)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_inventory_name ON public_restaurant_inventory(city,name)"
            )

    @staticmethod
    def _values(province: str, city: str, r: dict, stamp: str):
        return (
            r["provider_id"], province, city, r.get("name") or "이름없음",
            r.get("cuisine") or r.get("category") or "기타", r.get("primary_type"),
            r.get("road_address") or r.get("address"), r.get("phone"), r.get("x"), r.get("y"), r.get("place_url"),
            float(r.get("rating") or 0), int(r.get("user_rating_count") or 0), float(r.get("taste_score") or 0),
            int(r.get("query_hits") or 0),
            json.dumps(r.get("raw_json") or {"evidence": r.get("evidence") or {}}, ensure_ascii=False), stamp,
        )

    def replace_region(self, province: str, city: str, rows: list[dict]):
        stamp = now_iso()
        with self.connect() as con:
            con.execute("DELETE FROM verified_restaurants WHERE province=? AND city=?", (province, city))
            con.executemany(
                """INSERT INTO verified_restaurants(
                  provider_id,province,city,name,cuisine,primary_type,address,phone,x,y,place_url,
                  rating,user_rating_count,taste_score,query_hits,raw_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [self._values(province, city, r, stamp) for r in rows],
            )
        return len(rows)

    def upsert_region(self, province: str, city: str, rows: list[dict]):
        if not rows:
            return 0
        stamp = now_iso()
        with self.connect() as con:
            con.executemany(
                """INSERT INTO verified_restaurants(
                  provider_id,province,city,name,cuisine,primary_type,address,phone,x,y,place_url,
                  rating,user_rating_count,taste_score,query_hits,raw_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  province=excluded.province, city=excluded.city, name=excluded.name,
                  cuisine=excluded.cuisine, primary_type=excluded.primary_type,
                  address=excluded.address, phone=excluded.phone, x=excluded.x, y=excluded.y, place_url=excluded.place_url,
                  rating=excluded.rating, user_rating_count=excluded.user_rating_count,
                  taste_score=excluded.taste_score, query_hits=excluded.query_hits,
                  raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
                [self._values(province, city, r, stamp) for r in rows],
            )
        return len(rows)

    def get_region(self, province: str, city: str, limit: int = 300):
        with self.connect() as con:
            rows = con.execute(
                """SELECT provider_id,province,city,name,cuisine,primary_type,address,phone,x,y,place_url,
                   rating,user_rating_count,taste_score,query_hits,raw_json,updated_at
                   FROM verified_restaurants WHERE province=? AND city=?
                   ORDER BY taste_score DESC,user_rating_count DESC LIMIT ?""",
                (province, city, limit),
            ).fetchall()
        return [self._public_row(r) for r in rows]

    def count(self, province: str | None = None, city: str | None = None):
        where, args = [], []
        if province:
            where.append("province=?"); args.append(province)
        if city:
            where.append("city=?"); args.append(city)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM verified_restaurants" + clause, args).fetchone()[0])

    def search(self, q: str, limit: int = 100):
        like = f"%{q}%"
        with self.connect() as con:
            rows = con.execute(
                """SELECT provider_id,province,city,name,cuisine,primary_type,address,phone,x,y,place_url,
                   rating,user_rating_count,taste_score,query_hits,raw_json,updated_at
                   FROM verified_restaurants
                   WHERE name LIKE ? OR city LIKE ? OR cuisine LIKE ? OR address LIKE ? OR phone LIKE ?
                   ORDER BY taste_score DESC,user_rating_count DESC LIMIT ?""",
                (like, like, like, like, like, limit),
            ).fetchall()
        return [self._public_row(r) for r in rows]

    def replace_public_inventory(self, province: str, city: str, rows: list[dict]):
        stamp = now_iso()
        values = [
            (
                r["provider_id"], province, city, r.get("name") or "이름없음",
                r.get("category") or r.get("cuisine") or "음식점",
                r.get("road_address") or r.get("address"), r.get("phone"), r.get("status"),
                r.get("provider") or "general", r.get("business_type") or "음식점 인허가",
                json.dumps(r.get("raw_json") or {}, ensure_ascii=False), stamp,
            )
            for r in rows
        ]
        with self.connect() as con:
            con.execute("DELETE FROM public_restaurant_inventory WHERE province=? AND city=?", (province, city))
            if values:
                con.executemany(
                    """INSERT INTO public_restaurant_inventory(
                      provider_id,province,city,name,category,address,phone,status,source_provider,business_type,raw_json,fetched_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
        return len(values)

    def public_inventory_is_fresh(self, province: str, city: str, max_age_hours: int = 20) -> bool:
        with self.connect() as con:
            row = con.execute(
                "SELECT MAX(fetched_at) FROM public_restaurant_inventory WHERE province=? AND city=?",
                (province, city),
            ).fetchone()
        if not row or not row[0]:
            return False
        try:
            stamp = datetime.fromisoformat(str(row[0]))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return datetime.now(timezone.utc) - stamp <= timedelta(hours=max_age_hours)

    def get_public_inventory(self, province: str, city: str, limit: int = 30000):
        with self.connect() as con:
            rows = con.execute(
                """SELECT provider_id,province,city,name,category,address,phone,status,
                          source_provider,business_type,raw_json,fetched_at
                   FROM public_restaurant_inventory
                   WHERE province=? AND city=? ORDER BY name LIMIT ?""",
                (province, city, limit),
            ).fetchall()
        return [self._inventory_row(r) for r in rows]

    def search_public_inventory(self, province: str, city: str, q: str, limit: int = 50):
        like = f"%{q.strip()}%"
        with self.connect() as con:
            rows = con.execute(
                """SELECT provider_id,province,city,name,category,address,phone,status,
                          source_provider,business_type,raw_json,fetched_at
                   FROM public_restaurant_inventory
                   WHERE province=? AND city=? AND (name LIKE ? OR category LIKE ? OR address LIKE ?)
                   ORDER BY CASE WHEN name=? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END, name
                   LIMIT ?""",
                (province, city, like, like, like, q.strip(), like, limit),
            ).fetchall()
        return [self._inventory_row(r) for r in rows]

    def public_inventory_count(self, province: str, city: str) -> int:
        with self.connect() as con:
            return int(con.execute(
                "SELECT COUNT(*) FROM public_restaurant_inventory WHERE province=? AND city=?", (province, city)
            ).fetchone()[0])

    @staticmethod
    def _inventory_row(row):
        d = dict(row)
        try:
            raw = json.loads(d.get("raw_json") or "{}")
        except Exception:
            raw = {}
        provider = d.get("source_provider") or "general"
        business_type = d.get("business_type") or "음식점 인허가"
        return {
            "provider": provider, "provider_id": d["provider_id"], "province": d["province"], "city": d["city"],
            "name": d["name"], "category": d["category"], "cuisine": d["category"],
            "business_type": business_type, "address": d["address"], "road_address": d["address"],
            "phone": d["phone"], "x": None, "y": None, "place_url": None, "status": d["status"],
            "verified_public": False, "query_category": f"공공 {business_type}",
            "query_text": f"{d['city']} {business_type} 명부", "query_hits": 1,
            "discovery_mode": "official_inventory", "raw_json": raw, "fetched_at": d["fetched_at"],
        }

    @staticmethod
    def _public_row(row):
        d = dict(row)
        try:
            raw = json.loads(d.get("raw_json") or "{}")
        except Exception:
            raw = {}
        evidence = raw.get("evidence") or {}
        sources = evidence.get("sources") or []
        rating, reviews, score = float(d["rating"] or 0), int(d["user_rating_count"] or 0), float(d["taste_score"] or 0)
        if evidence.get("official_excellent") and len(sources) >= 2:
            label = "공식정보+다중출처"
        elif evidence.get("google", {}).get("strong") and len(evidence.get("specific_queries") or []) >= 3:
            label = "평가+지역반복"
        elif evidence.get("google", {}).get("high_volume") and not evidence.get("google", {}).get("strong"):
            label = "다수평가 인기"
        elif len(evidence.get("specific_queries") or []) >= 3:
            label = "지역 반복 노출"
        elif evidence.get("google", {}).get("strong"):
            label = "사용자 평가 강함"
        else:
            label = "공식정보 확인"
        return {
            "provider": "aggregate", "provider_id": d["provider_id"], "province": d["province"], "city": d["city"],
            "name": d["name"], "category": d["cuisine"], "cuisine": d["cuisine"],
            "business_type": d["primary_type"] or "restaurant", "primary_type": d["primary_type"],
            "address": d["address"], "road_address": d["address"], "phone": d.get("phone"), "x": d["x"], "y": d["y"],
            "place_url": d["place_url"], "status": "추천 맛집", "verified_public": bool(evidence.get("official_excellent")),
            "rating": rating, "user_rating_count": reviews, "taste_score": score,
            "query_hits": int(d["query_hits"] or 0), "source_count": len(sources), "sources": sources,
            "recommendation_label": label, "evidence": evidence, "updated_at": d["updated_at"],
        }
