"""Session storage for the Recast phone twin: what was seen, where, and when.

A walkthrough currently survives only as files under ~/plans/scans/. That is
enough to reload one session, but not to answer the questions a twin exists to
answer — did the chair count change between Tuesday and Friday, which floor was
walked, what scale calibration was in force. Those are queries, so they belong
in a database.

Three tables, all append-only and all timestamped:

  sessions            one row per walkthrough: when it started and ended, which
                      devices fed it, the floor, the calibration scale, the
                      final point count
  object_counts       a time series of "how many of each class", so a session
                      can be sampled repeatedly rather than only at its end
  scenegraph_objects  the flattened scenegraph — every confirmed object with
                      its world position, size, facing and stability tier

*The app must never wait on this module.* A digital twin that freezes because a
container is down is worse than one that forgets. Every public function catches
its own exceptions and returns a neutral value, connections use a short
timeout, and a failed connect trips a cooldown so the next hundred calls cost
nothing instead of two seconds each.

*Nothing here touches VSS.* The default DSN reaches the vss-vios-postgres
container over its unix socket (its `listen_addresses` is empty, so TCP on
127.0.0.1:5432 is a different, native PostgreSQL 16 — that mismatch is what
produced `fe_sendauth: no password supplied`). Objects live in their own
database `recast` and, within it, their own schema `recast`, so even a
misconfigured DSN pointing at nvcentralizedb cannot collide with VSS's public
tables.

Wiring into spark_app.py — three touch points, none of them load-bearing:

    import twin_db
    twin_db.init_schema()                     # at startup; False if DB is down

    # where SESSION flips to started, in recon_worker()
    SESSION.update(started=True, t0=time.time(), db_id=twin_db.start_session(
        LOCK_LEVEL[0] or ANCHOR.get("level") or SETUP.get("level"),
        [dv["id"] for dv in devs], CAL["scale_k"]))

    # in the end-of-stream block, right after SUMMARY.update(...)
    with _sglock:
        twin_db.save_scenegraph(SESSION.get("db_id"), twin_db.objects_from_scenegraph(SG))
        twin_db.save_counts(SESSION.get("db_id"), twin_db.counts_from_scenegraph(SG))
    twin_db.end_session(SESSION.get("db_id"), points=_m["total_points"], summary=_m)
    SESSION.update(started=False, db_id=None)

A session_id of None is a valid argument everywhere, so if the database was
unreachable at session start the end-of-session calls are silent no-ops rather
than a second failure to handle.
"""
import json
import logging
import os
import socket
import threading
import time

try:
    import psycopg2
    from psycopg2 import extensions as _pgext
    from psycopg2 import extras as _pgextras
except Exception as _e:                                   # pragma: no cover
    psycopg2 = None
    _IMPORT_ERR = _e
else:
    _IMPORT_ERR = None

LOG = logging.getLogger("twin_db")

# ---------------------------------------------------------------- connection
DB_NAME = os.environ.get("RECAST_PG_DB", "recast")
SCHEMA = os.environ.get("RECAST_PG_SCHEMA", "recast")
MAINTENANCE_DB = "postgres"

# The container publishes no ports and listens on no TCP address; its socket
# directory is bind-mounted onto the host, which is the only way in that does
# not require modifying VSS's config or shelling through `docker exec`.
_SOCKET_CANDIDATES = [
    os.environ.get("RECAST_PG_SOCKET_DIR"),
    os.path.expanduser("~/src/video-search-and-summarization/deploy/docker/"
                       "data-dir/data_log/vst/vst_data"),
    "/home/acer01/src/video-search-and-summarization/deploy/docker/"
    "data-dir/data_log/vst/vst_data",
    "/var/run/postgresql",
]
PG_USER = os.environ.get("RECAST_PG_USER", "vst")
CONNECT_TIMEOUT = int(os.environ.get("RECAST_PG_CONNECT_TIMEOUT", "3"))
STATEMENT_TIMEOUT_MS = int(os.environ.get("RECAST_PG_STATEMENT_TIMEOUT_MS", "5000"))
RETRY_COOLDOWN_S = float(os.environ.get("RECAST_PG_RETRY_COOLDOWN_S", "30"))

_lock = threading.RLock()
_conn = None
_next_try = 0.0          # circuit breaker: don't re-dial a dead server every frame
_logged = set()


def _socket_dir():
    for d in _SOCKET_CANDIDATES:
        if d and os.path.exists(os.path.join(d, ".s.PGSQL.5432")):
            return d
    return next((d for d in _SOCKET_CANDIDATES if d), "/var/run/postgresql")


def default_dsn(dbname=None):
    return "host=%s user=%s dbname=%s connect_timeout=%d" % (
        _socket_dir(), PG_USER, dbname or DB_NAME, CONNECT_TIMEOUT)


def dsn(dbname=None):
    """The configured DSN. RECAST_PG_DSN wins; otherwise the container socket."""
    d = os.environ.get("RECAST_PG_DSN")
    if not d:
        return default_dsn(dbname)
    if dbname is None:
        return d
    try:                       # swap the database, keep everything else
        parsed = _pgext.parse_dsn(d)
        parsed["dbname"] = dbname
        return _pgext.make_dsn(**parsed)
    except Exception:
        return d


def _note(msg, once=None, level=logging.WARNING):
    """Log once per distinct problem — this runs inside a render loop."""
    if once is not None:
        if once in _logged:
            return
        _logged.add(once)
    LOG.log(level, msg)
    print("[twin_db] %s" % msg, flush=True)


def _raw_connect(target_dsn):
    conn = psycopg2.connect(target_dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
    return conn


def connect():
    """A live connection to the recast database, or None if unavailable.

    Never raises and never blocks for longer than the connect timeout. After a
    failure the next attempt is deferred by RETRY_COOLDOWN_S, so a down
    database costs one timeout, not one per call.
    """
    global _conn, _next_try
    if psycopg2 is None:
        _note("psycopg2 unavailable (%s); session storage disabled" % _IMPORT_ERR,
              once="import")
        return None
    with _lock:
        if _conn is not None:
            if not _conn.closed:
                return _conn
            _conn = None
        if time.time() < _next_try:
            return None
        try:
            _conn = _raw_connect(dsn())
        except Exception as e:
            _conn = None
            _next_try = time.time() + RETRY_COOLDOWN_S
            _note("no database (%s); continuing without session storage"
                  % str(e).strip().replace("\n", " ")[:120], once="connect")
            return None
        _next_try = 0.0
        _logged.discard("connect")
        _note("connected: %s" % dsn(), once="connected", level=logging.INFO)
        return _conn


def close():
    """Drop the cached connection. Safe to call at shutdown, or never."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def _drop():
    global _conn, _next_try
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None
        _next_try = time.time() + RETRY_COOLDOWN_S


def _run(fn, default=None, what="query"):
    """Run fn(cursor) on the shared connection; swallow every failure."""
    conn = connect()
    if conn is None:
        return default
    try:
        with _lock, conn.cursor() as cur:
            return fn(cur)
    except Exception as e:
        _drop()
        _note("%s failed: %s" % (what, str(e).strip().replace("\n", " ")[:140]))
        return default


# -------------------------------------------------------------------- schema
DDL = """
CREATE SCHEMA IF NOT EXISTS {s};

CREATE TABLE IF NOT EXISTS {s}.sessions (
    session_id  bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    ended_at    timestamptz,
    level       text,
    devices     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    scale_k     double precision,
    points      bigint,
    note        text,
    summary     jsonb,
    host        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_started_at_idx
    ON {s}.sessions (started_at DESC);
CREATE INDEX IF NOT EXISTS sessions_level_idx ON {s}.sessions (level);

CREATE TABLE IF NOT EXISTS {s}.object_counts (
    id          bigserial PRIMARY KEY,
    session_id  bigint      NOT NULL
                REFERENCES {s}.sessions(session_id) ON DELETE CASCADE,
    observed_at timestamptz NOT NULL DEFAULT now(),
    cls         text        NOT NULL,
    n           integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS object_counts_session_idx
    ON {s}.object_counts (session_id);
CREATE INDEX IF NOT EXISTS object_counts_observed_idx
    ON {s}.object_counts (observed_at DESC);
CREATE INDEX IF NOT EXISTS object_counts_cls_idx ON {s}.object_counts (cls);

CREATE TABLE IF NOT EXISTS {s}.scenegraph_objects (
    id           bigserial PRIMARY KEY,
    session_id   bigint      NOT NULL
                 REFERENCES {s}.sessions(session_id) ON DELETE CASCADE,
    observed_at  timestamptz NOT NULL DEFAULT now(),
    cls          text        NOT NULL,
    x            double precision,
    y            double precision,
    z            double precision,
    size_w       double precision,
    size_d       double precision,
    size_h       double precision,
    yaw_deg      double precision,
    confidence   double precision,
    room_id      text,
    level        text,
    stability    smallint,
    observations integer,
    confirmed    boolean,
    raw          jsonb
);
CREATE INDEX IF NOT EXISTS scenegraph_objects_session_idx
    ON {s}.scenegraph_objects (session_id);
CREATE INDEX IF NOT EXISTS scenegraph_objects_observed_idx
    ON {s}.scenegraph_objects (observed_at DESC);
CREATE INDEX IF NOT EXISTS scenegraph_objects_cls_idx
    ON {s}.scenegraph_objects (cls);
CREATE INDEX IF NOT EXISTS scenegraph_objects_room_idx
    ON {s}.scenegraph_objects (room_id);
"""


def _ensure_database():
    """CREATE DATABASE recast if absent. Returns True if the database exists."""
    if psycopg2 is None:
        return False
    admin_dsn = dsn(MAINTENANCE_DB)
    try:
        conn = _raw_connect(admin_dsn)
    except Exception as e:
        # A DSN naming a database that already exists needs no maintenance
        # connection; only report if the target is unreachable too.
        _note("cannot reach %s to create the database: %s"
              % (MAINTENANCE_DB, str(e).strip().replace("\n", " ")[:120]))
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
            if cur.fetchone():
                return True
            try:
                cur.execute('CREATE DATABASE "%s"' % DB_NAME.replace('"', ''))
                _note("created database %s" % DB_NAME, level=logging.INFO)
            except Exception as e:
                # someone else created it between the check and the create
                if getattr(e, "pgcode", None) != "42P04":
                    raise
            return True
    except Exception as e:
        _note("create database failed: %s"
              % str(e).strip().replace("\n", " ")[:140])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def init_schema():
    """Create the database, schema and tables if absent. Idempotent, safe to
    call on every app start. Returns True when storage is ready."""
    if psycopg2 is None:
        _note("psycopg2 unavailable (%s); session storage disabled" % _IMPORT_ERR,
              once="import")
        return False
    if not os.environ.get("RECAST_PG_DSN"):
        # Only bootstrap the database when we own the DSN. A caller-supplied
        # DSN is assumed to point at a database that already exists.
        if not _ensure_database():
            return False
    global _next_try
    with _lock:
        _next_try = 0.0        # an explicit init should always retry
    ok = _run(lambda cur: (cur.execute(DDL.format(s=SCHEMA)), True)[1],
              default=False, what="init_schema")
    return bool(ok)


# ------------------------------------------------------------------- writing
def start_session(level, devices, scale_k, note=None):
    """Open a session row. Returns its id, or None if storage is unavailable."""
    devs = devices
    if devs is None:
        devs = []
    elif isinstance(devs, (str, bytes)):
        devs = [devs if isinstance(devs, str) else devs.decode("utf-8", "replace")]
    else:
        devs = [str(d) for d in devs]

    def go(cur):
        cur.execute(
            "INSERT INTO {s}.sessions (level, devices, scale_k, note, host) "
            "VALUES (%s, %s::jsonb, %s, %s, %s) RETURNING session_id".format(s=SCHEMA),
            (None if level is None else str(level), json.dumps(devs),
             _f(scale_k), note, socket.gethostname()))
        return int(cur.fetchone()[0])

    sid = _run(go, default=None, what="start_session")
    if sid is not None:
        _note("session %d open (level=%s, devices=%d, scale_k=%s)"
              % (sid, level, len(devs), _f(scale_k)), level=logging.INFO)
    return sid


def save_counts(session_id, counts):
    """Append one timestamped snapshot of {class: n}. Returns rows written."""
    if session_id is None or not counts:
        return 0
    rows = [(session_id, str(k), int(v)) for k, v in dict(counts).items()]

    def go(cur):
        _pgextras.execute_values(
            cur, "INSERT INTO {s}.object_counts (session_id, cls, n) "
                 "VALUES %s".format(s=SCHEMA), rows)
        return len(rows)

    return _run(go, default=0, what="save_counts") or 0


def save_scenegraph(session_id, objects):
    """Append the flattened scenegraph. Returns rows written.

    Accepts either the app's live Track fields (`cls`, `pos`, `size`, `yaw`,
    `conf`, `n`) or the serialised form from SceneGraph.to_dict()
    (`position`, `size_m`, `yaw_deg`, `confidence`, `observations`).
    """
    if session_id is None or not objects:
        return 0
    rows = []
    for o in objects:
        try:
            rows.append(_object_row(session_id, o))
        except Exception as e:
            _note("skipped malformed object: %s" % str(e)[:80])
    if not rows:
        return 0

    def go(cur):
        _pgextras.execute_values(
            cur,
            "INSERT INTO {s}.scenegraph_objects "
            "(session_id, cls, x, y, z, size_w, size_d, size_h, yaw_deg, "
            " confidence, room_id, level, stability, observations, confirmed, raw) "
            "VALUES %s".format(s=SCHEMA), rows)
        return len(rows)

    return _run(go, default=0, what="save_scenegraph") or 0


def end_session(session_id, points=None, summary=None):
    """Stamp the session closed with its final point count and summary."""
    if session_id is None:
        return False

    def go(cur):
        cur.execute(
            "UPDATE {s}.sessions SET ended_at = now(), "
            "points = COALESCE(%s, points), "
            "summary = COALESCE(%s::jsonb, summary) "
            "WHERE session_id = %s".format(s=SCHEMA),
            (None if points is None else int(points),
             None if summary is None else json.dumps(summary, default=str),
             int(session_id)))
        return cur.rowcount > 0

    ok = _run(go, default=False, what="end_session")
    if ok:
        _note("session %s closed (points=%s)" % (session_id, points),
              level=logging.INFO)
    return bool(ok)


# ------------------------------------------------------------------- reading
def recent_sessions(n=10):
    """Newest sessions with their object totals, for on-screen display."""
    def go(cur):
        cur.execute(
            "SELECT s.session_id, s.started_at, s.ended_at, s.level, s.devices, "
            "       s.scale_k, s.points, s.note, s.summary, "
            "       (SELECT count(*) FROM {s}.scenegraph_objects o "
            "          WHERE o.session_id = s.session_id) AS objects, "
            "       (SELECT count(DISTINCT c.cls) FROM {s}.object_counts c "
            "          WHERE c.session_id = s.session_id) AS classes "
            "  FROM {s}.sessions s "
            " ORDER BY s.started_at DESC LIMIT %s".format(s=SCHEMA), (int(n),))
        cols = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            d["duration_s"] = (None if not d["ended_at"] else
                               round((d["ended_at"] - d["started_at"])
                                     .total_seconds(), 1))
            out.append(d)
        return out

    return _run(go, default=[], what="recent_sessions") or []


def session_counts(session_id, latest_only=True):
    """{class: n} for a session — the last snapshot, or every row summed."""
    def go(cur):
        if latest_only:
            cur.execute(
                "SELECT cls, n FROM {s}.object_counts "
                " WHERE session_id = %s AND observed_at = "
                "       (SELECT max(observed_at) FROM {s}.object_counts "
                "         WHERE session_id = %s) "
                " ORDER BY n DESC".format(s=SCHEMA),
                (int(session_id), int(session_id)))
        else:
            cur.execute(
                "SELECT cls, sum(n) FROM {s}.object_counts "
                " WHERE session_id = %s GROUP BY cls ORDER BY 2 DESC".format(s=SCHEMA),
                (int(session_id),))
        return {r[0]: int(r[1]) for r in cur.fetchall()}

    return _run(go, default={}, what="session_counts") or {}


def session_objects(session_id, limit=1000):
    """The stored scenegraph rows for a session."""
    def go(cur):
        cur.execute(
            "SELECT cls, x, y, z, size_w, size_d, size_h, yaw_deg, confidence, "
            "       room_id, level, stability, observations, confirmed, observed_at "
            "  FROM {s}.scenegraph_objects WHERE session_id = %s "
            " ORDER BY cls, id LIMIT %s".format(s=SCHEMA),
            (int(session_id), int(limit)))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    return _run(go, default=[], what="session_objects") or []


# ------------------------------------------------------------------- helpers
def _f(v):
    try:
        return None if v is None else float(v)
    except Exception:
        return None


def _i(v):
    try:
        return None if v is None else int(v)
    except Exception:
        return None


def _xyz(o):
    for key in ("position", "pos", "xyz"):
        p = o.get(key)
        if p is not None:
            p = list(p)
            return _f(p[0]), _f(p[1]), _f(p[2] if len(p) > 2 else None)
    return _f(o.get("x")), _f(o.get("y")), _f(o.get("z"))


def _size(o):
    for key in ("size_m", "size", "extent"):
        s = o.get(key)
        if s is None:
            continue
        try:
            s = list(s)
            return _f(s[0]), _f(s[1]), _f(s[2])
        except TypeError:                 # a scalar size: treat as a cube edge
            v = _f(s)
            return v, v, v
    return None, None, None


def _object_row(session_id, o):
    o = dict(o)
    cls = o.get("cls") or o.get("class") or o.get("label")
    if not cls:
        raise ValueError("object has no class")
    x, y, z = _xyz(o)
    w, d, h = _size(o)
    return (int(session_id), str(cls), x, y, z, w, d, h,
            _f(o.get("yaw_deg", o.get("yaw"))),
            _f(o.get("confidence", o.get("conf"))),
            o.get("room_id"), o.get("level"),
            _i(o.get("stability")),
            _i(o.get("observations", o.get("n"))),
            None if o.get("confirmed") is None else bool(o.get("confirmed")),
            json.dumps(o, default=str))


def objects_from_scenegraph(sg, confirmed_only=True):
    """Flatten a live SceneGraph into rows for save_scenegraph().

    Reads Track objects directly rather than the serialised tree, so objects
    that never landed inside a room polygon are stored too — a chair whose
    room is unknown is still a chair that was there.
    """
    try:
        import scenegraph3d
        stability = scenegraph3d.STABILITY
        transient = scenegraph3d.TRANSIENT
    except Exception:
        stability, transient = {}, set()
    out = []
    for t in getattr(sg, "tracks", []) or []:
        if confirmed_only and not getattr(t, "confirmed", False):
            continue
        if t.cls in transient:
            continue
        pos = [float(v) for v in t.pos]
        size = None if t.size is None else [float(v) for v in t.size]
        out.append(dict(cls=t.cls, x=pos[0], y=pos[1], z=pos[2],
                        size=size, yaw=t.yaw, confidence=float(t.conf),
                        room_id=t.room_id, level=t.level,
                        stability=stability.get(t.cls, 0),
                        observations=int(t.n), confirmed=bool(t.confirmed)))
    return out


def counts_from_scenegraph(sg):
    """{class: n} over confirmed, non-transient tracks — what the HUD shows."""
    try:
        import scenegraph3d
        transient = scenegraph3d.TRANSIENT
    except Exception:
        transient = set()
    counts = {}
    for t in getattr(sg, "tracks", []) or []:
        if getattr(t, "confirmed", False) and t.cls not in transient:
            counts[t.cls] = counts.get(t.cls, 0) + 1
    return counts


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    import random
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("dsn:", dsn())
    assert init_schema(), "init_schema failed"
    assert init_schema(), "init_schema is not idempotent"

    sid = start_session("level1", ["phone_a", "phone_b"], 1.07, note="self-test")
    assert sid, "no session id"
    objs = [dict(cls=random.choice(["chair", "desk", "monitor"]),
                 x=random.uniform(0, 9), y=random.uniform(0, 5), z=0.8,
                 size=[0.6, 0.6, 0.9], yaw=random.uniform(0, 180),
                 confidence=0.6, room_id="level1_room_00", level="level1",
                 stability=2, observations=5, confirmed=True)
            for _ in range(20)]
    counts = {}
    for o in objs:
        counts[o["cls"]] = counts.get(o["cls"], 0) + 1
    assert save_scenegraph(sid, objs) == 20
    assert save_counts(sid, counts) == len(counts)
    assert end_session(sid, points=123456, summary=dict(note="self-test"))

    back = session_counts(sid)
    assert back == counts, "counts round-trip mismatch: %s != %s" % (back, counts)
    assert len(session_objects(sid)) == 20
    for r in recent_sessions(3):
        print(" session %(session_id)s  %(level)s  objects=%(objects)s "
              "points=%(points)s" % r)
    close()
    print("\nSELF-TEST OK")
