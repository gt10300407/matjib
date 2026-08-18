from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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

    def init_schema(self):
        with self.connect() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS verified_restaurants(
              provider_id TEXT PRIMARY KEY,
              province TEXT NOT NULL,
              city TEXT NOT NULL,
              name TEXT NOT NULL,
              cuisine TEXT,
              primary_type TEXT,
              address TEXT,
              x TEXT,
              y TEXT,
              place_url TEXT,
              rating REAL NOT NULL,
              user_rating_count INTEGER NOT NULL,
              taste_score REAL NOT NULL,
              query_hits INTEGER NOT NULL DEFAULT 1,
              raw_json TEXT,
              updated_at TEXT NOT NULL
            )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_verified_region ON verified_restaurants(province,city)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_verified_cuisine ON verified_restaurants(cuisine)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_verified_score ON verified_restaurants(taste_score DESC)")

    def replace_region(self, province: str, city: str, rows: list[dict]):
        stamp = now_iso()
        with self.connect() as con:
            con.execute("DELETE FROM verified_restaurants WHERE province=? AND city=?", (province, city))
            con.executemany("""
            INSERT INTO verified_restaurants(
              provider_id,province,city,name,cuisine,primary_type,address,x,y,place_url,
              rating,user_rating_count,taste_score,query_hits,raw_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [
                (
                    r["provider_id"], province, city, r.get("name") or "이름없음",
                    r.get("cuisine") or r.get("category") or "기타", r.get("primary_type"),
                    r.get("road_address") or r.get("address"), r.get("x"), r.get("y"), r.get("place_url"),
                    float(r.get("rating") or 0), int(r.get("user_rating_count") or 0),
                    float(r.get("taste_score") or 0), int(r.get("query_hits") or 1),
                    json.dumps(r.get("raw_json") or {}, ensure_ascii=False), stamp,
                )
                for r in rows
            ])
        return len(rows)

    def get_region(self, province: str, city: str, limit: int = 300):
        with self.connect() as con:
            rows = con.execute("""
            SELECT provider_id,province,city,name,cuisine,primary_type,address,x,y,place_url,
                   rating,user_rating_count,taste_score,query_hits,updated_at
            FROM verified_restaurants
            WHERE province=? AND city=?
            ORDER BY taste_score DESC, user_rating_count DESC
            LIMIT ?
            """, (province, city, limit)).fetchall()
        return [self._public_row(r) for r in rows]

    def count(self, province: str | None = None, city: str | None = None):
        where=[]
        args=[]
        if province:
            where.append("province=?")
            args.append(province)
        if city:
            where.append("city=?")
            args.append(city)
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        with self.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM verified_restaurants"+clause,args).fetchone()[0])

    def search(self, q: str, limit: int = 100):
        like=f"%{q}%"
        with self.connect() as con:
            rows=con.execute("""
            SELECT provider_id,province,city,name,cuisine,primary_type,address,x,y,place_url,
                   rating,user_rating_count,taste_score,query_hits,updated_at
            FROM verified_restaurants
            WHERE name LIKE ? OR city LIKE ? OR cuisine LIKE ? OR address LIKE ?
            ORDER BY taste_score DESC, user_rating_count DESC
            LIMIT ?
            """,(like,like,like,like,limit)).fetchall()
        return [self._public_row(r) for r in rows]

    @staticmethod
    def _public_row(row):
        d=dict(row)
        return {
            "provider":"google",
            "provider_id":d["provider_id"],
            "province":d["province"],
            "city":d["city"],
            "name":d["name"],
            "category":d["cuisine"],
            "cuisine":d["cuisine"],
            "business_type":d["primary_type"] or "restaurant",
            "primary_type":d["primary_type"],
            "address":d["address"],
            "road_address":d["address"],
            "phone":None,
            "x":d["x"],
            "y":d["y"],
            "place_url":d["place_url"],
            "status":"검증 맛집",
            "verified_public":False,
            "rating":float(d["rating"]),
            "user_rating_count":int(d["user_rating_count"]),
            "taste_score":float(d["taste_score"]),
            "query_hits":int(d["query_hits"]),
            "updated_at":d["updated_at"],
        }
