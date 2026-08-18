from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

def now_iso():
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path: Path):
        self.path=Path(path)
        self.last_repair_reason=None
        self.last_backup_path=None

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con=sqlite3.connect(str(self.path), timeout=15.0)
        con.execute("PRAGMA busy_timeout=15000")
        con.row_factory=sqlite3.Row
        return con

    def _repair_unopenable_db(self, exc):
        from .paths import quarantine_bad_db
        self.last_repair_reason=f"{type(exc).__name__}: {exc}"
        backup=quarantine_bad_db(self.path)
        self.last_backup_path=str(backup) if backup else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_dir():
            raise RuntimeError(f"DB 경로가 디렉터리라 복구하지 못했어: {self.path}")

    def connect(self):
        try:
            return self._open()
        except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError, PermissionError) as exc:
            self._repair_unopenable_db(exc)
            return self._open()

    def init_schema(self):
        with self.connect() as con:
            con.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS regional_foods(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              province TEXT NOT NULL, city TEXT NOT NULL, name TEXT NOT NULL,
              subtitle TEXT, emoji TEXT, source_label TEXT, source_url TEXT,
              UNIQUE(province,city,name)
            );
            CREATE TABLE IF NOT EXISTS restaurants(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              provider TEXT NOT NULL, provider_id TEXT NOT NULL,
              province TEXT NOT NULL, city TEXT NOT NULL, name TEXT NOT NULL,
              category TEXT, business_type TEXT, address TEXT, road_address TEXT,
              phone TEXT, x TEXT, y TEXT, place_url TEXT, status TEXT,
              verified_public INTEGER NOT NULL DEFAULT 0, raw_json TEXT,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(provider,provider_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rest_region ON restaurants(province,city);
            CREATE INDEX IF NOT EXISTS idx_rest_name ON restaurants(name);
            CREATE TABLE IF NOT EXISTS refresh_log(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              province TEXT NOT NULL, city TEXT NOT NULL, refreshed_at TEXT NOT NULL,
              new_count INTEGER NOT NULL, changed_count INTEGER NOT NULL,
              total_seen INTEGER NOT NULL, detail_json TEXT
            );
            """)

    def seed_foods(self, rows):
        with self.connect() as con:
            con.executemany("""
            INSERT INTO regional_foods(province,city,name,subtitle,emoji,source_label,source_url)
            VALUES(:province,:city,:name,:subtitle,:emoji,:source_label,:source_url)
            ON CONFLICT(province,city,name) DO UPDATE SET
              subtitle=excluded.subtitle, emoji=excluded.emoji,
              source_label=excluded.source_label, source_url=excluded.source_url
            """, rows)

    def get_foods(self, province, city):
        with self.connect() as con:
            rows=con.execute("""
              SELECT name,subtitle,emoji,source_label,source_url
              FROM regional_foods WHERE province=? AND city=? ORDER BY id
            """,(province,city)).fetchall()
        return [dict(r) for r in rows]

    def get_restaurants(self, province, city, limit=500):
        with self.connect() as con:
            rows=con.execute("""
              SELECT provider,provider_id,name,category,business_type,address,road_address,
                     phone,x,y,place_url,status,verified_public,first_seen_at,last_seen_at,updated_at
              FROM restaurants
              WHERE province=? AND city=?
              ORDER BY CASE provider WHEN 'excellent' THEN 0 WHEN 'kakao' THEN 1 ELSE 2 END, id DESC
              LIMIT ?
            """,(province,city,limit)).fetchall()
        today=now_iso()[:10]
        out=[]
        for r in rows:
            d=dict(r)
            d["verified_public"]=bool(d["verified_public"])
            d["is_new"]=d["first_seen_at"][:10]==today
            out.append(d)
        return out

    def get_stats(self, province=None, city=None):
        where=[]
        args=[]
        if province:
            where.append("province=?")
            args.append(province)
        if city:
            where.append("city=?")
            args.append(city)
        clause=(" WHERE "+" AND ".join(where)) if where else ""

        last_error=None
        for attempt in range(3):
            try:
                with self.connect() as con:
                    restaurant_count=con.execute("SELECT COUNT(*) FROM restaurants"+clause, args).fetchone()[0]
                    food_count=con.execute("SELECT COUNT(*) FROM regional_foods"+clause, args).fetchone()[0]
                return {"restaurants": restaurant_count,"representative_foods": food_count,"markets": 0,"markets_ready": False}
            except sqlite3.OperationalError as exc:
                last_error=exc
                if "no such table" in str(exc).lower():
                    self.init_schema(); continue
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    import time; time.sleep(0.2*(attempt+1)); continue
                raise
        raise last_error

    def search_restaurants(self, q, limit=200):
        like=f"%{q}%"
        with self.connect() as con:
            rows=con.execute("""
              SELECT provider,provider_id,province,city,name,category,road_address,phone,
                     place_url,status,verified_public
              FROM restaurants
              WHERE name LIKE ? OR road_address LIKE ? OR category LIKE ?
              ORDER BY id DESC LIMIT ?
            """,(like,like,like,limit)).fetchall()
        return [dict(r) for r in rows]

    def upsert_restaurant(self,row):
        stamp=now_iso()
        with self.connect() as con:
            old=con.execute("""
              SELECT name,category,business_type,address,road_address,phone,x,y,place_url,status,verified_public
              FROM restaurants WHERE provider=? AND provider_id=?
            """,(row["provider"],row["provider_id"])).fetchone()
            comp=(row.get("name"),row.get("category"),row.get("business_type"),row.get("address"),row.get("road_address"),row.get("phone"),row.get("x"),row.get("y"),row.get("place_url"),row.get("status"),int(bool(row.get("verified_public"))))
            if old is None:
                con.execute("""
                  INSERT INTO restaurants(
                    provider,provider_id,province,city,name,category,business_type,address,road_address,
                    phone,x,y,place_url,status,verified_public,raw_json,first_seen_at,last_seen_at,updated_at
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,(row["provider"],row["provider_id"],row["province"],row["city"],row.get("name") or "이름없음",row.get("category"),row.get("business_type"),row.get("address"),row.get("road_address"),row.get("phone"),row.get("x"),row.get("y"),row.get("place_url"),row.get("status"),int(bool(row.get("verified_public"))),json.dumps(row.get("raw_json") or {},ensure_ascii=False),stamp,stamp,stamp))
                return "new"
            changed=tuple(old)!=comp
            con.execute("""
              UPDATE restaurants SET province=?,city=?,name=?,category=?,business_type=?,
                address=?,road_address=?,phone=?,x=?,y=?,place_url=?,status=?,verified_public=?,
                raw_json=?,last_seen_at=?,updated_at=?
              WHERE provider=? AND provider_id=?
            """,(row["province"],row["city"],row.get("name") or "이름없음",row.get("category"),row.get("business_type"),row.get("address"),row.get("road_address"),row.get("phone"),row.get("x"),row.get("y"),row.get("place_url"),row.get("status"),int(bool(row.get("verified_public"))),json.dumps(row.get("raw_json") or {},ensure_ascii=False),stamp,stamp,row["provider"],row["provider_id"]))
            return "changed" if changed else "same"

    def _reset_for_write_failure(self, exc):
        from .paths import quarantine_bad_db
        self.last_repair_reason=f"write failure: {type(exc).__name__}: {exc}"
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(self.path)+suffix).unlink(missing_ok=True)
            except Exception:
                pass
        backup=quarantine_bad_db(self.path)
        self.last_backup_path=str(backup) if backup else None
        self.init_schema()

    def upsert_many(self, rows):
        rows=list(rows)
        if not rows:
            return {"new":0,"changed":0,"same":0,"persisted":0}
        last_exc=None
        for attempt in range(2):
            try:
                stamp=now_iso()
                new_count=changed_count=same_count=0
                with self.connect() as con:
                    for row in rows:
                        old=con.execute("""
                          SELECT name,category,business_type,address,road_address,phone,
                                 x,y,place_url,status,verified_public
                          FROM restaurants
                          WHERE provider=? AND provider_id=?
                        """,(row["provider"],row["provider_id"])).fetchone()
                        comp=(row.get("name"),row.get("category"),row.get("business_type"),row.get("address"),row.get("road_address"),row.get("phone"),row.get("x"),row.get("y"),row.get("place_url"),row.get("status"),int(bool(row.get("verified_public"))))
                        if old is None:
                            con.execute("""
                              INSERT INTO restaurants(
                                provider,provider_id,province,city,name,category,business_type,
                                address,road_address,phone,x,y,place_url,status,verified_public,
                                raw_json,first_seen_at,last_seen_at,updated_at
                              ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,(row["provider"],row["provider_id"],row["province"],row["city"],row.get("name") or "이름없음",row.get("category"),row.get("business_type"),row.get("address"),row.get("road_address"),row.get("phone"),row.get("x"),row.get("y"),row.get("place_url"),row.get("status"),int(bool(row.get("verified_public"))),json.dumps(row.get("raw_json") or {},ensure_ascii=False),stamp,stamp,stamp))
                            new_count+=1
                        else:
                            changed=tuple(old)!=comp
                            con.execute("""
                              UPDATE restaurants SET
                                province=?,city=?,name=?,category=?,business_type=?,address=?,
                                road_address=?,phone=?,x=?,y=?,place_url=?,status=?,
                                verified_public=?,raw_json=?,last_seen_at=?,updated_at=?
                              WHERE provider=? AND provider_id=?
                            """,(row["province"],row["city"],row.get("name") or "이름없음",row.get("category"),row.get("business_type"),row.get("address"),row.get("road_address"),row.get("phone"),row.get("x"),row.get("y"),row.get("place_url"),row.get("status"),int(bool(row.get("verified_public"))),json.dumps(row.get("raw_json") or {},ensure_ascii=False),stamp,stamp,row["provider"],row["provider_id"]))
                            if changed: changed_count+=1
                            else: same_count+=1
                return {"new":new_count,"changed":changed_count,"same":same_count,"persisted":len(rows)}
            except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError, PermissionError) as exc:
                last_exc=exc
                if attempt==0:
                    self._reset_for_write_failure(exc)
                    continue
                raise
        raise last_exc

    def mark_kakao_public_verified(self, provider_id):
        with self.connect() as con:
            con.execute("""
              UPDATE restaurants SET verified_public=1, updated_at=?
              WHERE provider='kakao' AND provider_id=?
            """,(now_iso(),provider_id))

    def add_refresh_log(self, province, city, new_count, changed_count, total_seen, detail):
        with self.connect() as con:
            con.execute("""
              INSERT INTO refresh_log(province,city,refreshed_at,new_count,changed_count,total_seen,detail_json)
              VALUES(?,?,?,?,?,?,?)
            """,(province,city,now_iso(),new_count,changed_count,total_seen,json.dumps(detail,ensure_ascii=False)))

    def get_last_refresh(self, province, city):
        with self.connect() as con:
            r=con.execute("""
              SELECT refreshed_at,new_count,changed_count,total_seen,detail_json
              FROM refresh_log WHERE province=? AND city=? ORDER BY id DESC LIMIT 1
            """,(province,city)).fetchone()
        return dict(r) if r else None
