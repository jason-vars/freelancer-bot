from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

DB_PATH = "bot.sqlite3"

SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  url TEXT,
  description TEXT,
  currency TEXT,
  budget_min REAL,
  budget_max REAL,
  bid_count INTEGER,
  bid_avg REAL,
  skills TEXT,
  created_at TEXT,
  score INTEGER DEFAULT 0,
  status TEXT DEFAULT 'new',
  filter_reason TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS bids (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  bid_id INTEGER,
  amount REAL,
  period_days INTEGER,
  milestone_percent INTEGER,
  proposal TEXT,
  created_at TEXT,
  status TEXT DEFAULT 'placed',
  FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS bot_state (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  extracted_project_json TEXT,
  project_detail_json TEXT,
  result_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_projects (
  id INTEGER PRIMARY KEY,
  seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_messages (
  message_id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL,
  project_id INTEGER,
  html TEXT NOT NULL,
  read_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_bids_created ON bids(created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_created ON webhook_events(created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_project ON webhook_events(project_id);
'''

def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    # Some restricted environments fail with rollback-journal create/delete cycles.
    # WAL mode is typically more robust and performs better for frequent commits.
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except sqlite3.Error:
        # Keep defaults if pragmas are not supported by the filesystem/runtime.
        pass
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Lightweight migrations for existing DB files.
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "bid_count" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN bid_count INTEGER")
        if "bid_avg" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN bid_avg REAL")
        if "filter_reason" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN filter_reason TEXT")
        if "raw_json" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN raw_json TEXT")
    except sqlite3.Error:
        pass
    # Enforce one bid per project at the DB level so two concurrent webhook
    # deliveries cannot both place a bid (the application-level
    # bid_exists_for_project check has a narrow TOCTOU race; this closes it).
    # Created only when no duplicate project_ids already exist, so it is a no-op
    # on legacy DBs that violate the invariant rather than raising on startup.
    try:
        dup = conn.execute(
            "SELECT 1 FROM bids GROUP BY project_id HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if dup is None:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_bids_project_id ON bids(project_id)"
            )
    except sqlite3.Error:
        pass
    # One-time seed of the dedup set from any projects already stored, so the
    # first poll after this migration does not re-evaluate the existing backlog.
    # Guarded by a state flag so it does NOT run on every init_db — otherwise it
    # would re-mark stored projects as seen and silently undo `reset-seen`.
    try:
        seeded = conn.execute(
            "SELECT 1 FROM bot_state WHERE key = 'seen_projects_seeded'"
        ).fetchone()
        if seeded is None:
            conn.execute(
                "INSERT OR IGNORE INTO seen_projects(id, seen_at) SELECT id, ? FROM projects",
                (utc_now_str(),),
            )
            conn.execute(
                "INSERT INTO bot_state(key, value) VALUES('seen_projects_seeded', '1') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
    except sqlite3.Error:
        pass
    conn.commit()

def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _to_db_datetime_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        # Already in target format
        if len(v) == 19 and v[4] == "-" and v[7] == "-" and v[10] == " ":
            return v
        # Numeric strings (epoch seconds or decimals)
        try:
            ts = float(v)
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        # ISO8601 strings (handle trailing Z)
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None

def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None

def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO bot_state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()

def is_project_seen(conn: sqlite3.Connection, project_id: int) -> bool:
    """True if this project id has already been evaluated by a prior poll. Used
    for order-independent dedup: unlike a max-id high-water mark, this never skips
    a relevant project just because its id is lower than one seen earlier."""
    row = conn.execute(
        "SELECT 1 FROM seen_projects WHERE id = ? LIMIT 1", (project_id,)
    ).fetchone()
    return row is not None

def mark_project_seen(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_projects(id, seen_at) VALUES(?, ?)",
        (project_id, utc_now_str()),
    )
    conn.commit()

def clear_seen_projects(conn: sqlite3.Connection) -> int:
    """Forget every previously-evaluated project id so the next poll re-scans the
    whole feed. Returns the number of ids cleared. Use after loosening filters."""
    cur = conn.execute("DELETE FROM seen_projects")
    conn.commit()
    return int(cur.rowcount)

def record_sent_message(conn: sqlite3.Connection, message_id: int, chat_id: str,
                        project_id: int | None, html: str) -> None:
    """Remember a notification we sent, keyed by Telegram message_id, so the
    update listener can re-render it (mark read) when its button is tapped."""
    conn.execute(
        "INSERT OR REPLACE INTO sent_messages(message_id, chat_id, project_id, html, read_at) "
        "VALUES(?, ?, ?, ?, NULL)",
        (int(message_id), str(chat_id), project_id, html),
    )
    conn.commit()

def get_sent_message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT message_id, chat_id, project_id, html, read_at FROM sent_messages WHERE message_id = ?",
        (int(message_id),),
    ).fetchone()

def mark_message_read(conn: sqlite3.Connection, message_id: int, read_at: str) -> None:
    conn.execute(
        "UPDATE sent_messages SET read_at = ? WHERE message_id = ?",
        (read_at, int(message_id)),
    )
    conn.commit()

def upsert_project(conn: sqlite3.Connection, p: dict) -> None:
    conn.execute(
        '''
        INSERT INTO projects(id, title, url, description, currency, budget_min, budget_max, bid_count, bid_avg, skills, created_at, score, status, filter_reason, raw_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0), COALESCE(?, 'new'), ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          title=excluded.title,
          url=excluded.url,
          description=excluded.description,
          currency=excluded.currency,
          budget_min=excluded.budget_min,
          budget_max=excluded.budget_max,
          bid_count=excluded.bid_count,
          bid_avg=excluded.bid_avg,
          skills=excluded.skills,
          created_at=excluded.created_at,
          filter_reason=excluded.filter_reason,
          raw_json=excluded.raw_json
        ''',
        (
            p["id"],
            p.get("title", ""),
            p.get("url"),
            p.get("description"),
            p.get("currency"),
            p.get("budget_min"),
            p.get("budget_max"),
            p.get("bid_count"),
            p.get("bid_avg"),
            p.get("skills"),
            _to_db_datetime_str(p.get("created_at")),
            p.get("score"),
            p.get("status"),
            p.get("filter_reason"),
            p.get("raw_json"),
        ),
    )
    conn.commit()

def get_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM projects WHERE id=?", (int(project_id),)
    ).fetchone()

def get_project_by_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    """Look up a project by its stored ``seo_url`` (the bare ``category/slug`` path
    Freelancer uses, e.g. ``ui-design/app-application``). Used by the browser
    userscript, which derives the slug from the freelancer project page URL. Matches
    with or without a trailing slash; newest row wins if somehow duplicated."""
    u = (url or "").strip().strip("/")
    if not u:
        return None
    return conn.execute(
        "SELECT * FROM projects WHERE url=? OR url=? ORDER BY created_at DESC LIMIT 1",
        (u, u + "/"),
    ).fetchone()

def set_project_score_and_status(conn: sqlite3.Connection, project_id: int, score: int, status: str) -> None:
    conn.execute(
        "UPDATE projects SET score=?, status=? WHERE id=?",
        (score, status, project_id),
    )
    conn.commit()

def delete_project(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()

def list_projects_by_status(conn: sqlite3.Connection, status: str, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM projects WHERE status=? ORDER BY created_at DESC LIMIT ?",
        (status, limit),
    ).fetchall()


def list_all_projects(conn: sqlite3.Connection, status: str | None = None, limit: int = 500) -> list[sqlite3.Row]:
    """Every stored project, newest first. Pass ``status`` to restrict to one
    status (e.g. 'new', 'filtered', 'bid'); None returns all statuses."""
    if status:
        return conn.execute(
            "SELECT * FROM projects WHERE status=? ORDER BY COALESCE(created_at, '') DESC, id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM projects ORDER BY COALESCE(created_at, '') DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def count_projects_by_status(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT COALESCE(status, 'new') AS status, COUNT(*) AS c FROM projects "
        "GROUP BY COALESCE(status, 'new') ORDER BY c DESC",
    ).fetchall()


def set_project_filtered(conn: sqlite3.Connection, project_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE projects SET status='filtered', score=0, filter_reason=? WHERE id=?",
        (reason, project_id),
    )
    conn.commit()


def delete_projects_by_currency(conn: sqlite3.Connection, currencies: list[str]) -> int:
    """Delete any stored projects whose currency is in ``currencies`` (case-insensitive).

    Useful for purging rows saved before currency filtering existed. Returns the
    number of rows removed.
    """
    codes = [(c or "").strip().upper() for c in currencies if (c or "").strip()]
    if not codes:
        return 0
    placeholders = ",".join("?" for _ in codes)
    cur = conn.execute(
        f"DELETE FROM projects WHERE UPPER(currency) IN ({placeholders})",
        codes,
    )
    conn.commit()
    return cur.rowcount


def list_filtered_projects(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM projects WHERE status='filtered' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def count_projects_by_filter_reason(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT filter_reason, COUNT(*) AS c FROM projects WHERE status='filtered' "
        "GROUP BY filter_reason ORDER BY c DESC",
    ).fetchall()

def insert_bid(conn: sqlite3.Connection, project_id: int, bid_id: int | None, amount: float, period_days: int,
               milestone_percent: int, proposal: str, status: str) -> None:
    conn.execute(
        '''
        INSERT INTO bids(project_id, bid_id, amount, period_days, milestone_percent, proposal, created_at, status)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (project_id, bid_id, amount, period_days, milestone_percent, proposal, utc_now_str(), status),
    )
    conn.commit()

def get_latest_bid(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row | None:
    """The most recent bid/draft row for a project (the proposal that was sent),
    or None if none exists. Used by the web UI to show what was submitted."""
    return conn.execute(
        "SELECT bid_id, amount, period_days, milestone_percent, proposal, status, created_at "
        "FROM bids WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (int(project_id),),
    ).fetchone()

def bid_exists_for_project(conn: sqlite3.Connection, project_id: int) -> bool:
    """True if any bid row (real, dry-run, or saved draft) already exists for this
    project. Used to make webhook processing idempotent: every Telegram notification
    is preceded by an insert_bid, so a prior bid row means the project was already
    handled and a redelivered/duplicate webhook should be skipped."""
    row = conn.execute(
        "SELECT 1 FROM bids WHERE project_id = ? LIMIT 1", (project_id,)
    ).fetchone()
    return row is not None

def count_bids_today(conn: sqlite3.Connection) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM bids WHERE substr(created_at, 1, 10) = ?",
        (today,),
    ).fetchone()
    return int(row["c"]) if row else 0

def insert_webhook_event(
    conn: sqlite3.Connection,
    event_type: str,
    payload_json: str,
    project_id: int | None = None,
    extracted_project_json: str | None = None,
    project_detail_json: str | None = None,
    result_json: str | None = None,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO webhook_events(project_id, event_type, payload_json, extracted_project_json, project_detail_json, result_json, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ''',
        (project_id, event_type, payload_json, extracted_project_json, project_detail_json, result_json, utc_now_str()),
    )
    conn.commit()
    return int(cur.lastrowid)

def update_webhook_event(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    project_id: int | None = None,
    extracted_project_json: str | None = None,
    project_detail_json: str | None = None,
    result_json: str | None = None,
) -> None:
    conn.execute(
        '''
        UPDATE webhook_events
        SET project_id = COALESCE(?, project_id),
            extracted_project_json = COALESCE(?, extracted_project_json),
            project_detail_json = COALESCE(?, project_detail_json),
            result_json = COALESCE(?, result_json)
        WHERE id = ?
        ''',
        (project_id, extracted_project_json, project_detail_json, result_json, event_id),
    )
    conn.commit()

def list_webhook_events(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        '''
        SELECT id, project_id, event_type, created_at, payload_json, extracted_project_json, project_detail_json, result_json
        FROM webhook_events
        ORDER BY id DESC
        LIMIT ?
        ''',
        (limit,),
    ).fetchall()

def get_webhook_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return conn.execute(
        '''
        SELECT id, project_id, event_type, created_at, payload_json, extracted_project_json, project_detail_json, result_json
        FROM webhook_events
        WHERE id = ?
        ''',
        (event_id,),
    ).fetchone()
