"""
memory_store.py  –  Persistent long-term memory for Jarvis.

Stores important user facts on disk and retrieves relevant memories
for future responses. Memory file defaults to D: drive on Windows.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

_MEMORY_LOCK = threading.Lock()
_pg_schema_initialized = False  # cache: skip CREATE TABLE after first success
DECAY_INTERVAL_HOURS = int(os.getenv("JARVIS_MEMORY_DECAY_INTERVAL_HOURS", "24"))
DECAY_STEP = float(os.getenv("JARVIS_MEMORY_DECAY_STEP", "0.03"))
MIN_IMPORTANCE = float(os.getenv("JARVIS_MEMORY_MIN_IMPORTANCE", "0.2"))


def _memory_backend() -> str:
    """
    Memory backend selector.
    - CIPHER_MEMORY_BACKEND=postgres forces PostgreSQL mode
    - Else if CIPHER_MEMORY_DATABASE_URL or DATABASE_URL exists, use postgres
    - Fallback: json file
    """
    explicit = os.getenv("CIPHER_MEMORY_BACKEND", "").strip().lower()
    if explicit in {"postgres", "json"}:
        return explicit

    if os.getenv("CIPHER_MEMORY_DATABASE_URL") or os.getenv("DATABASE_URL"):
        return "postgres"
    return "json"


def _pg_database_url() -> str:
    raw = (os.getenv("CIPHER_MEMORY_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        return raw

    # Handle copy-paste style URLs like: postgresql://user:[password]@host:5432/db
    # by removing the square brackets around password.
    fixed = re.sub(r":\[([^\]]+)\]@", r":\1@", raw)
    return fixed


def _pg_sslmode() -> str:
    return os.getenv("CIPHER_DB_SSLMODE", "require").strip() or "require"


def _pg_connect():
    db_url = _pg_database_url()
    if not db_url:
        raise RuntimeError("Postgres memory backend enabled but no CIPHER_MEMORY_DATABASE_URL / DATABASE_URL configured.")

    try:
        import psycopg  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Postgres memory backend requires psycopg. Add it to requirements and install dependencies."
        ) from exc

    connect_timeout = int(os.getenv("CIPHER_DB_CONNECT_TIMEOUT", "8"))
    return psycopg.connect(db_url, sslmode=_pg_sslmode(), connect_timeout=connect_timeout)


def _pg_init_schema(conn) -> None:
    global _pg_schema_initialized
    if _pg_schema_initialized:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cipher_memory (
                id UUID PRIMARY KEY,
                fact TEXT NOT NULL,
                fact_norm TEXT NOT NULL UNIQUE,
                importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                pinned BOOLEAN NOT NULL DEFAULT FALSE,
                tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                source TEXT NOT NULL DEFAULT 'user_message',
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                last_used_at TIMESTAMPTZ NULL,
                use_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cipher_memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
    conn.commit()
    _pg_schema_initialized = True


def _pg_get_meta(conn, key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM cipher_memory_meta WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else None


def _pg_set_meta(conn, key: str, value: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cipher_memory_meta (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value
            """,
            (key, value),
        )
    conn.commit()


def _apply_decay_if_due_pg(conn) -> bool:
    now = datetime.now(timezone.utc)
    last_decay_raw = _pg_get_meta(conn, "last_decay_at")
    last_decay = _parse_iso_datetime(last_decay_raw)

    if last_decay is None:
        _pg_set_meta(conn, "last_decay_at", _utc_now_iso())
        return True

    interval = timedelta(hours=max(1, DECAY_INTERVAL_HOURS))
    elapsed = now - last_decay
    if elapsed < interval:
        return False

    steps = int(elapsed.total_seconds() // interval.total_seconds())
    decay_amount = steps * DECAY_STEP

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cipher_memory
            SET importance = GREATEST(0.0, importance - %s),
                updated_at = NOW()
            WHERE pinned = FALSE
            """,
            (decay_amount,),
        )
        cur.execute(
            "DELETE FROM cipher_memory WHERE pinned = FALSE AND importance < %s",
            (MIN_IMPORTANCE,),
        )
    conn.commit()
    _pg_set_meta(conn, "last_decay_at", _utc_now_iso())
    return True


def _pg_fetch_memories(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, fact, importance, pinned, tags, source,
                   created_at, updated_at, last_used_at, use_count
            FROM cipher_memory
            """
        )
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "fact": r[1],
                "importance": float(r[2]),
                "pinned": bool(r[3]),
                "tags": list(r[4] or []),
                "source": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
                "updated_at": r[7].isoformat() if r[7] else None,
                "last_used_at": r[8].isoformat() if r[8] else None,
                "use_count": int(r[9] or 0),
            }
        )
    return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _memory_file_path() -> str:
    env_path = os.getenv("JARVIS_MEMORY_PATH", "").strip()
    if env_path:
        return env_path

    if os.name == "nt" and os.path.exists("D:\\"):
        return r"D:\JarvisMemory\jarvis_memory.json"

    backend_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(backend_dir, "jarvis_memory.json")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_db() -> dict[str, Any]:
    path = _memory_file_path()
    if not os.path.exists(path):
        return {"version": 1, "memories": []}

    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {"version": 1, "memories": []}

    if not isinstance(data, dict):
        return {"version": 1, "memories": []}
    data.setdefault("version", 2)
    data.setdefault("memories", [])
    data.setdefault("last_decay_at", None)

    for mem in data["memories"]:
        mem.setdefault("pinned", False)

    return data


def _save_db(db: dict[str, Any]) -> None:
    path = _memory_file_path()
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _apply_decay_if_due(db: dict[str, Any]) -> bool:
    """Decay non-pinned memories over time. Returns True if DB changed."""
    now = datetime.now(timezone.utc)
    last_decay = _parse_iso_datetime(db.get("last_decay_at"))
    if last_decay is None:
        db["last_decay_at"] = _utc_now_iso()
        return True

    interval = timedelta(hours=max(1, DECAY_INTERVAL_HOURS))
    elapsed = now - last_decay
    if elapsed < interval:
        return False

    steps = int(elapsed.total_seconds() // interval.total_seconds())
    decay_amount = steps * DECAY_STEP
    changed = False

    memories: list[dict[str, Any]] = db.get("memories", [])
    retained: list[dict[str, Any]] = []
    for mem in memories:
        if mem.get("pinned", False):
            retained.append(mem)
            continue

        old_importance = float(mem.get("importance", 0.5))
        new_importance = max(0.0, old_importance - decay_amount)
        if abs(new_importance - old_importance) > 1e-9:
            mem["importance"] = round(new_importance, 4)
            mem["updated_at"] = _utc_now_iso()
            changed = True

        if new_importance >= MIN_IMPORTANCE:
            retained.append(mem)
        else:
            changed = True

    if len(retained) != len(memories):
        changed = True

    db["memories"] = retained
    db["last_decay_at"] = _utc_now_iso()
    return changed


def _normalize_fact(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    stop = {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "are", "am", "i",
        "my", "me", "it", "that", "this", "you", "we", "our", "be", "was", "were", "as", "at", "by",
    }
    return {t for t in tokens if len(t) >= 3 and t not in stop}


def extract_important_facts(message: str) -> list[dict[str, Any]]:
    """Extract potentially important user facts from a raw user message."""
    text = (message or "").strip()
    if not text:
        return []

    patterns: list[tuple[str, str, float, list[str]]] = [
        (r"\bremember\s+(?:that\s+)?(.+)", "{0}", 0.98, ["explicit-memory"]),
        (r"\bmy\s+name\s+is\s+(.+)", "User's name is {0}", 0.95, ["identity", "name"]),
        (r"\bi\s+am\s+from\s+(.+)", "User is from {0}", 0.85, ["location"]),
        (r"\bi\s+live\s+in\s+(.+)", "User lives in {0}", 0.85, ["location"]),
        (r"\bmy\s+birthday\s+is\s+(.+)", "User's birthday is {0}", 0.9, ["profile"]),
        (r"\bi\s+like\s+(.+)", "User likes {0}", 0.75, ["preference"]),
        (r"\bi\s+love\s+(.+)", "User loves {0}", 0.8, ["preference"]),
        (r"\bi\s+prefer\s+(.+)", "User prefers {0}", 0.85, ["preference"]),
        (r"\bmy\s+favorite\s+(.+?)\s+is\s+(.+)", "User's favorite {0} is {1}", 0.86, ["preference"]),
    ]

    found: list[dict[str, Any]] = []
    for pattern, template, importance, tags in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue

        groups = [g.strip().strip(".!") for g in m.groups() if g and g.strip()]
        if not groups:
            continue

        fact = template.format(*groups)
        fact = re.sub(r"\s+", " ", fact).strip()
        if len(fact) < 6 or len(fact) > 280:
            continue

        found.append(
            {
                "fact": fact,
                "importance": importance,
                "tags": tags,
                "source": "user_message",
            }
        )

    # Also store compact profile-like statements: "my X is Y"
    if not found:
        m = re.search(r"\bmy\s+([a-zA-Z0-9 _-]{2,40})\s+is\s+(.{2,120})", text, flags=re.IGNORECASE)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip().strip(".!")
            fact = f"User's {key} is {value}"
            found.append(
                {
                    "fact": fact,
                    "importance": 0.72,
                    "tags": ["profile"],
                    "source": "user_message",
                }
            )

    return found


def _upsert_memories(db: dict[str, Any], new_facts: list[dict[str, Any]]) -> int:
    memories: list[dict[str, Any]] = db.get("memories", [])
    by_norm = {_normalize_fact(m.get("fact", "")): m for m in memories}
    added = 0

    for item in new_facts:
        fact = item.get("fact", "").strip()
        if not fact:
            continue
        norm = _normalize_fact(fact)
        if not norm:
            continue

        existing = by_norm.get(norm)
        if existing:
            existing["importance"] = max(float(existing.get("importance", 0.5)), float(item.get("importance", 0.5)))
            existing["updated_at"] = _utc_now_iso()
            existing_tags = set(existing.get("tags", []))
            existing_tags.update(item.get("tags", []))
            existing["tags"] = sorted(existing_tags)
            continue

        new_mem = {
            "id": str(uuid.uuid4()),
            "fact": fact,
            "importance": float(item.get("importance", 0.5)),
            "pinned": bool(item.get("pinned", False)),
            "tags": item.get("tags", []),
            "source": item.get("source", "user_message"),
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "last_used_at": None,
            "use_count": 0,
        }
        memories.append(new_mem)
        by_norm[norm] = new_mem
        added += 1

    db["memories"] = memories[-1000:]  # hard cap to keep storage reasonable
    return added


def remember_from_message(message: str) -> int:
    """Extract and persist important facts from a user message. Returns count added."""
    facts = extract_important_facts(message)
    if not facts:
        return 0

    if _memory_backend() == "postgres":
        with _MEMORY_LOCK:
            with _pg_connect() as conn:
                _pg_init_schema(conn)
                _apply_decay_if_due_pg(conn)

                added = 0
                for item in facts:
                    fact = item.get("fact", "").strip()
                    if not fact:
                        continue
                    fact_norm = _normalize_fact(fact)
                    if not fact_norm:
                        continue

                    mem_id = str(uuid.uuid4())
                    tags = list(item.get("tags", []))
                    importance = float(item.get("importance", 0.5))
                    source = str(item.get("source", "user_message"))

                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM cipher_memory WHERE fact_norm = %s", (fact_norm,))
                        existed = cur.fetchone() is not None
                        cur.execute(
                            """
                            INSERT INTO cipher_memory (
                                id, fact, fact_norm, importance, pinned, tags, source,
                                created_at, updated_at, last_used_at, use_count
                            )
                            VALUES (
                                %s, %s, %s, %s, FALSE, %s, %s,
                                NOW(), NOW(), NULL, 0
                            )
                            ON CONFLICT (fact_norm)
                            DO UPDATE SET
                                fact = EXCLUDED.fact,
                                importance = GREATEST(cipher_memory.importance, EXCLUDED.importance),
                                tags = ARRAY(SELECT DISTINCT UNNEST(cipher_memory.tags || EXCLUDED.tags)),
                                updated_at = NOW()
                            """,
                            (mem_id, fact, fact_norm, importance, tags, source),
                        )

                    if not existed:
                        added += 1

                conn.commit()
                return added

    with _MEMORY_LOCK:
        db = _load_db()
        _apply_decay_if_due(db)
        added = _upsert_memories(db, facts)
        _save_db(db)
    return added


def get_relevant_memories(query: str, limit: int = 6) -> list[str]:
    """Retrieve memory facts ranked by relevance to the current user query."""
    if _memory_backend() == "postgres":
        with _MEMORY_LOCK:
            with _pg_connect() as conn:
                _pg_init_schema(conn)
                _apply_decay_if_due_pg(conn)
                memories = _pg_fetch_memories(conn)
                if not memories:
                    return []

                query_tokens = _tokenize(query)
                ranked: list[tuple[float, dict[str, Any]]] = []
                for mem in memories:
                    fact = mem.get("fact", "")
                    mem_tokens = _tokenize(fact)
                    overlap = len(query_tokens.intersection(mem_tokens)) if query_tokens else 0
                    importance = float(mem.get("importance", 0.5))
                    use_count = int(mem.get("use_count", 0))
                    score = overlap * 2.0 + importance + (0.04 * use_count)
                    ranked.append((score, mem))

                ranked.sort(key=lambda x: x[0], reverse=True)
                selected = [m for s, m in ranked if s > 1.0][:limit]
                if not selected:
                    selected = [m for _, m in ranked[: min(limit, 2)]]

                if selected:
                    ids = [m["id"] for m in selected]
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE cipher_memory
                            SET last_used_at = NOW(),
                                use_count = use_count + 1
                            WHERE id::text = ANY(%s)
                            """,
                            (ids,),
                        )
                    conn.commit()

                return [m.get("fact", "") for m in selected if m.get("fact")]

    with _MEMORY_LOCK:
        db = _load_db()
        changed = _apply_decay_if_due(db)
        memories: list[dict[str, Any]] = db.get("memories", [])
        if not memories:
            if changed:
                _save_db(db)
            return []

        query_tokens = _tokenize(query)
        ranked: list[tuple[float, dict[str, Any]]] = []

        for mem in memories:
            fact = mem.get("fact", "")
            mem_tokens = _tokenize(fact)
            overlap = len(query_tokens.intersection(mem_tokens)) if query_tokens else 0
            importance = float(mem.get("importance", 0.5))
            use_count = int(mem.get("use_count", 0))

            score = overlap * 2.0 + importance + (0.04 * use_count)
            ranked.append((score, mem))

        ranked.sort(key=lambda x: x[0], reverse=True)

        # Prefer overlap matches; if none, still keep a couple of highest-importance memories.
        selected = [m for s, m in ranked if s > 1.0][:limit]
        if not selected:
            selected = [m for _, m in ranked[: min(limit, 2)]]

        now = _utc_now_iso()
        for mem in selected:
            mem["last_used_at"] = now
            mem["use_count"] = int(mem.get("use_count", 0)) + 1

        _save_db(db)
        return [m.get("fact", "") for m in selected if m.get("fact")]


def list_memories(limit: int = 100) -> list[dict[str, Any]]:
    if _memory_backend() == "postgres":
        with _MEMORY_LOCK:
            with _pg_connect() as conn:
                _pg_init_schema(conn)
                _apply_decay_if_due_pg(conn)
                memories = _pg_fetch_memories(conn)
                memories_sorted = sorted(
                    memories,
                    key=lambda m: (bool(m.get("pinned", False)), float(m.get("importance", 0.0))),
                    reverse=True,
                )
                return memories_sorted[: max(1, limit)]

    with _MEMORY_LOCK:
        db = _load_db()
        changed = _apply_decay_if_due(db)
        memories: list[dict[str, Any]] = db.get("memories", [])
        memories_sorted = sorted(
            memories,
            key=lambda m: (bool(m.get("pinned", False)), float(m.get("importance", 0.0))),
            reverse=True,
        )
        if changed:
            _save_db(db)
        return memories_sorted[: max(1, limit)]


def set_memory_pinned(memory_id: str, pinned: bool = True) -> bool:
    if _memory_backend() == "postgres":
        with _MEMORY_LOCK:
            with _pg_connect() as conn:
                _pg_init_schema(conn)
                _apply_decay_if_due_pg(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE cipher_memory
                        SET pinned = %s,
                            updated_at = NOW()
                        WHERE id::text = %s
                        """,
                        (bool(pinned), memory_id),
                    )
                    updated = cur.rowcount > 0
                conn.commit()
                return updated

    with _MEMORY_LOCK:
        db = _load_db()
        _apply_decay_if_due(db)
        memories: list[dict[str, Any]] = db.get("memories", [])
        for mem in memories:
            if mem.get("id") == memory_id:
                mem["pinned"] = bool(pinned)
                mem["updated_at"] = _utc_now_iso()
                _save_db(db)
                return True
        return False


def clear_all_memories() -> None:
    if _memory_backend() == "postgres":
        with _MEMORY_LOCK:
            with _pg_connect() as conn:
                _pg_init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM cipher_memory")
                conn.commit()
                _pg_set_meta(conn, "last_decay_at", _utc_now_iso())
        return

    with _MEMORY_LOCK:
        _save_db({"version": 2, "memories": [], "last_decay_at": _utc_now_iso()})


def get_memory_stats() -> dict[str, Any]:
    if _memory_backend() == "postgres":
        with _MEMORY_LOCK:
            with _pg_connect() as conn:
                _pg_init_schema(conn)
                _apply_decay_if_due_pg(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM cipher_memory")
                    count = int(cur.fetchone()[0])
                    cur.execute("SELECT COUNT(*) FROM cipher_memory WHERE pinned = TRUE")
                    pinned_count = int(cur.fetchone()[0])

                safe_url = _pg_database_url()
                if "@" in safe_url:
                    safe_url = safe_url.split("@", 1)[-1]

                return {
                    "backend": "postgres",
                    "database": safe_url,
                    "count": count,
                    "pinned_count": pinned_count,
                    "decay_interval_hours": DECAY_INTERVAL_HOURS,
                    "decay_step": DECAY_STEP,
                }

    with _MEMORY_LOCK:
        db = _load_db()
        changed = _apply_decay_if_due(db)
        memories = db.get("memories", [])
        pinned_count = len([m for m in memories if m.get("pinned", False)])
        if changed:
            _save_db(db)
        return {
            "backend": "json",
            "path": _memory_file_path(),
            "count": len(memories),
            "pinned_count": pinned_count,
            "decay_interval_hours": DECAY_INTERVAL_HOURS,
            "decay_step": DECAY_STEP,
        }
