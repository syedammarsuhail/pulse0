"""Pulse0 - a personal, OpenTelemetry-native observability tool.

Ingests OTLP (HTTP, JSON or protobuf) for traces, logs and metrics, stores them in
SQLite, and serves a resource-centric UI: services (RED metrics), service map,
trace waterfall, log search, metric explorer, alert rules and synthetic checks.
"""
import json, os, sqlite3, threading, time, ssl, socket, urllib.request, urllib.error, urllib.parse, base64
import collections, contextvars, hashlib, hmac, ipaddress, re, secrets
from contextlib import contextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, HTTPException, Depends, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, Literal
import uuid

DB_PATH = os.environ.get("PULSE0_DB", os.path.join(os.path.dirname(__file__), "pulse0.db"))
DATABASE_URL = os.environ.get("PULSE0_DATABASE_URL", "")
USE_PG = bool(DATABASE_URL)
PG_SCHEMA = os.environ.get("PULSE0_PG_SCHEMA", "pulse0")

if USE_PG:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    _IntegrityError = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
else:
    _IntegrityError = sqlite3.IntegrityError
STATIC = os.path.join(os.path.dirname(__file__), "static")
RETENTION_HOURS = float(os.environ.get("PULSE0_RETENTION_HOURS", "72"))
AUTH_TOKEN = os.environ.get("PULSE0_TOKEN", "")  # optional bearer token for ingest + API

# ---------------------------------------------------------------- Tier / licensing
# PULSE0_TIER: "community" (free, limited) or "pro" (paid, unlimited).
# A valid PULSE0_LICENSE_KEY (validated by the enterprise module) activates Pro.
# On first install, a 14-day Pro trial is activated automatically.
_LICENSE_KEY = os.environ.get("PULSE0_LICENSE_KEY", "")
_ENTERPRISE = False
try:
    from enterprise.license import validate_license
    _ENTERPRISE = True
    _LICENSE_VALID = validate_license(_LICENSE_KEY) if _LICENSE_KEY else False
except ImportError:
    _LICENSE_VALID = bool(_LICENSE_KEY)

# Sovereign / data-residency mode. When on, Pulse0 makes ZERO outbound calls to any third-party
# vendor: in particular the Claude "Ask Pulse0" assistant is hard-disabled, so telemetry can never
# reach Anthropic, regardless of whether ANTHROPIC_API_KEY is set. Operator-configured endpoints
# that live inside the customer's own network — their OIDC identity provider (SSO), their alert
# webhooks, their synthetic-check targets — are unaffected; Pulse0 never phones home on its own.
SOVEREIGN = os.environ.get("PULSE0_SOVEREIGN", "").strip().lower() in ("1", "true", "yes", "on")

TRIAL_DAYS = 14
_TRIAL_START = 0
_TRIAL_ACTIVE = False
_TRIAL_DAYS_LEFT = 0

def _init_trial(db_path):
    """Check/initialize trial state from the meta table. Called after DB init."""
    global _TRIAL_START, _TRIAL_ACTIVE, _TRIAL_DAYS_LEFT, TIER, IS_PRO, RETENTION_HOURS
    if _LICENSE_VALID or os.environ.get("PULSE0_TIER", "").strip().lower() == "pro":
        return
    try:
        now = int(time.time())
        if USE_PG:
            with _pg_pool.connection() as pc:
                pc.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
                pc.execute("INSERT INTO meta(key,value) VALUES('trial_start',%s) ON CONFLICT (key) DO NOTHING", (str(now),))
                _TRIAL_START = int(pc.execute("SELECT value FROM meta WHERE key='trial_start'").fetchone()[0])
        else:
            c = sqlite3.connect(db_path, check_same_thread=False)
            c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            row = c.execute("SELECT value FROM meta WHERE key='trial_start'").fetchone()
            if row:
                _TRIAL_START = int(row[0])
            else:
                _TRIAL_START = now
                c.execute("INSERT INTO meta(key,value) VALUES('trial_start',?)", (str(now),))
                c.commit()
            c.close()
        elapsed = now - _TRIAL_START
        _TRIAL_DAYS_LEFT = max(0, TRIAL_DAYS - int(elapsed / 86400))
        _TRIAL_ACTIVE = _TRIAL_DAYS_LEFT > 0
        if _TRIAL_ACTIVE:
            TIER = "pro"
            IS_PRO = True
            RETENTION_HOURS = float(os.environ.get("PULSE0_RETENTION_HOURS", "72"))
    except Exception:
        pass

TIER = "pro" if _LICENSE_VALID else os.environ.get("PULSE0_TIER", "community").strip().lower()
if TIER not in ("community", "pro"):
    TIER = "community"
IS_PRO = TIER == "pro"
def _print_tier():
    if IS_PRO and _ENTERPRISE and _LICENSE_VALID:
        print(f"Pulse0 Pro activated (enterprise module, license validated)")
    elif IS_PRO and _TRIAL_ACTIVE:
        print(f"Pulse0 Pro trial ({_TRIAL_DAYS_LEFT} days remaining)")
    elif IS_PRO:
        print("Pulse0 Pro activated (PULSE0_TIER override)")
    else:
        if _TRIAL_START and not _TRIAL_ACTIVE:
            print("Pulse0 Community tier (Pro trial expired, set PULSE0_LICENSE_KEY to upgrade)")
        else:
            print("Pulse0 Community tier (set PULSE0_LICENSE_KEY to activate Pro)")

if SOVEREIGN:
    print("Pulse0 Sovereign mode ON: no outbound vendor calls; Claude assistant disabled; telemetry stays in your environment")

# Community-tier limits (Pro is unlimited)
TIER_LIMITS = {
    "max_services": 5,
    "max_dashboards": 3,
    "max_alerts": 3,
    "max_slos": 1,
    "max_synthetics": 1,
    "retention_days": 7,
}
if not IS_PRO:
    RETENTION_HOURS = min(RETENTION_HOURS, TIER_LIMITS["retention_days"] * 24)


def _tier_check(resource: str, table: str):
    """Raise 403 if the community tier limit is reached for a resource."""
    if IS_PRO:
        return
    limit = TIER_LIMITS.get(f"max_{resource}")
    if limit is None:
        return
    count = q(f"SELECT COUNT(*) n FROM {table}")[0]["n"]
    if count >= limit:
        raise HTTPException(
            403,
            {"error": "tier_limit",
             "message": f"Community tier allows up to {limit} {resource}. Upgrade to Pro for unlimited.",
             "limit": limit, "current": count, "tier": "community",
             "upgrade_url": "https://pulse0.dev/pricing"})


def _tier_gate(feature: str):
    """Block a Pro-only feature on community tier."""
    if IS_PRO:
        return
    raise HTTPException(
        403,
        {"error": "pro_required",
         "message": f"{feature} is available on the Pro tier. Upgrade to unlock.",
         "feature": feature, "tier": "community",
         "upgrade_url": "https://pulse0.dev/pricing"})

# Deployment mode. "selfhosted" (default) is the classic single-tenant Pulse0: one SQLite file, optional
# PULSE0_TOKEN. "hosted" is Pulse-hosted multi-tenant: organizations -> projects (one SQLite silo each),
# hashed API keys, per-org quotas. See the "hosted mode" section below.
MODE = os.environ.get("PULSE0_MODE", "selfhosted").strip().lower()
if MODE not in ("selfhosted", "hosted"):
    raise RuntimeError("PULSE0_MODE must be 'selfhosted' or 'hosted'")
HOSTED = MODE == "hosted"
ADMIN_TOKEN = os.environ.get("PULSE0_ADMIN_TOKEN", "")  # bootstrap platform admin (hosted mode only)
DATA_DIR = os.environ.get("PULSE0_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "tenants"))
if HOSTED and not ADMIN_TOKEN:
    raise RuntimeError("PULSE0_ADMIN_TOKEN is required when PULSE0_MODE=hosted")

def _jload(v):
    """json.loads that passes through dicts (Postgres JSONB returns dicts directly)."""
    return v if isinstance(v, (dict, list)) else json.loads(v or "{}")


# ---------------------------------------------------------------- Postgres SQL translation
_PG_TABLES = ("spans", "logs", "metrics", "alert_rules", "alert_events",
              "synthetics", "synthetic_runs", "dashboards", "audit", "slos")

def _pg_sql(sql):
    """Translate SQLite-flavoured SQL to Postgres dialect."""
    if not USE_PG:
        return sql
    # Parameter placeholders: ? -> %s (but not inside strings)
    out, i, in_str = [], 0, False
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == '?' and not in_str:
            out.append('%s')
        else:
            out.append(ch)
        i += 1
    sql = ''.join(out)

    # Schema-qualify table names
    for t in _PG_TABLES:
        sql = re.sub(r'\b(FROM|JOIN|INTO|UPDATE|TABLE)\s+' + t + r'\b',
                      lambda m: m.group(1) + ' ' + PG_SCHEMA + '.' + t, sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bINSERT\s+INTO\s+' + PG_SCHEMA + r'\.' + t + r'\s+VALUES',
                      'INSERT INTO ' + PG_SCHEMA + '.' + t + ' VALUES', sql, flags=re.IGNORECASE)
        # ON ... table(col) references in indexes
        sql = re.sub(r'\bON\s+' + t + r'\(', 'ON ' + PG_SCHEMA + '.' + t + '(', sql, flags=re.IGNORECASE)

    # json_extract(col, '$."key"') -> col->>'key'  and  json_extract(col, '$.key') -> col->>'key'
    def _jx(m):
        col, key = m.group(1), m.group(2)
        key = key.strip('"')
        return f"{col}->>'{key}'"
    sql = re.sub(r'''json_extract\(\s*(\w+)\s*,\s*'\$\.["']?([^'"]+)["']?'\s*\)''', _jx, sql)

    # json_extract(col, %s) with a bound '$."key"' path -> jsonpath lookup returning text
    sql = re.sub(r"json_extract\(\s*(\w+)\s*,\s*%s\s*\)", r"(jsonb_path_query_first(\1, %s::jsonpath) #>> '{}')", sql)

    # CAST(col->>'key' AS TEXT) -> col->>'key'  (already text in Postgres)
    sql = re.sub(r"CAST\(\s*([\w.]+->>'[^']+')\s+AS\s+TEXT\)", r'\1', sql, flags=re.IGNORECASE)

    # SUM(expr=val) -> SUM((expr=val)::int)  — Postgres boolean not summable
    sql = re.sub(r"SUM\(([^()=]+=[^()]+)\)", r'SUM((\1)::int)', sql)
    # SUM((ts_ns>=%s)::int) is fine but SUM(ts_ns>=%s) needs the same treatment
    sql = re.sub(r"SUM\(((?:[^()>]|>(?!=))+>=[^()]+)\)", r'SUM((\1)::int)', sql)
    # Fix double-casting: SUM(((expr)::int)::int) -> SUM((expr)::int)
    sql = re.sub(r"\(\(([^()]+)\)::int\)::int", r'(\1)::int', sql)

    # (parent_id<>'') needs no translation, it works in both

    # ORDER BY (parent_id<>'') -> ORDER BY (parent_id<>'')::int  (Postgres can't sort boolean directly
    # in the same way SQLite sorts 0/1, but actually Postgres CAN order by boolean: false < true)
    # So this is fine.

    return sql



_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS spans(
  trace_id TEXT, span_id TEXT, parent_id TEXT, service TEXT, name TEXT, kind INTEGER,
  start_ns INTEGER, end_ns INTEGER, dur_ms REAL, status INTEGER, status_msg TEXT,
  attrs TEXT, res TEXT);
CREATE INDEX IF NOT EXISTS ix_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS ix_spans_time ON spans(start_ns);
CREATE INDEX IF NOT EXISTS ix_spans_svc ON spans(service, start_ns);
CREATE TABLE IF NOT EXISTS logs(
  ts_ns INTEGER, service TEXT, severity INTEGER, sev_text TEXT, body TEXT,
  trace_id TEXT, span_id TEXT, attrs TEXT, res TEXT);
CREATE INDEX IF NOT EXISTS ix_logs_time ON logs(ts_ns);
CREATE INDEX IF NOT EXISTS ix_logs_err ON logs(ts_ns, service) WHERE severity>=17;
CREATE TABLE IF NOT EXISTS metrics(
  ts_ns INTEGER, service TEXT, name TEXT, unit TEXT, mtype TEXT, value REAL, attrs TEXT, res TEXT);
CREATE INDEX IF NOT EXISTS ix_metrics ON metrics(name, ts_ns);
CREATE TABLE IF NOT EXISTS alert_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, signal TEXT, expr TEXT, op TEXT,
  threshold REAL, window_s INTEGER, service TEXT, webhook TEXT, enabled INTEGER DEFAULT 1,
  state TEXT DEFAULT 'ok', last_value REAL, last_eval INTEGER, since INTEGER);
CREATE TABLE IF NOT EXISTS alert_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id INTEGER, ts INTEGER, state TEXT, value REAL, msg TEXT);
CREATE TABLE IF NOT EXISTS synthetics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, interval_s INTEGER DEFAULT 60,
  expect_status INTEGER DEFAULT 200, max_ms REAL DEFAULT 2000, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS synthetic_runs(
  check_id INTEGER, ts INTEGER, ok INTEGER, status INTEGER, ms REAL, ssl_days REAL, err TEXT);
CREATE INDEX IF NOT EXISTS ix_synth ON synthetic_runs(check_id, ts);
CREATE TABLE IF NOT EXISTS dashboards(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, panels TEXT, updated INTEGER);
CREATE TABLE IF NOT EXISTS audit(
  ts INTEGER, actor TEXT, role TEXT, via TEXT, method TEXT, path TEXT, status INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_audit ON audit(ts);
CREATE TABLE IF NOT EXISTS slos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, service TEXT,
  objective_pct REAL, latency_ms REAL, window_days INTEGER DEFAULT 30);
"""


def _open_db(path):
    c = sqlite3.connect(path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


# ---- Postgres pool (when USE_PG) ----
_pg_pool = None
if USE_PG:
    _pg_pool = ConnectionPool(DATABASE_URL, min_size=2, max_size=10, open=True,
                              kwargs={"options": f"-c search_path={PG_SCHEMA},public"})


def _pg_ddl():
    """The SQLite schema above, translated to Postgres types."""
    ddl = "\n".join(ln for ln in _SCHEMA.splitlines() if not ln.startswith("PRAGMA"))
    ddl = ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    ddl = re.sub(r"\bINTEGER\b", "BIGINT", ddl)
    ddl = re.sub(r"\bREAL\b", "DOUBLE PRECISION", ddl)
    return re.sub(r"\b(attrs|res) TEXT", r"\1 JSONB", ddl)


if USE_PG:
    if not PG_SCHEMA.isidentifier():
        raise RuntimeError("PULSE0_PG_SCHEMA must be a plain identifier")
    with _pg_pool.connection() as _c:
        _c.execute(f'CREATE SCHEMA IF NOT EXISTS "{PG_SCHEMA}"')
        _c.execute(_pg_ddl())

# ---- SQLite single-tenant (when not USE_PG) ----
_lock = threading.Lock()
_conn = None if USE_PG else _open_db(DB_PATH)
if USE_PG:
    _init_trial(None)
elif _conn:
    _init_trial(DB_PATH)
_print_tier()

# Which SQLite silo the current request / worker iteration reads and writes. None = the default
# (self-hosted) database. The hosted-mode auth middleware sets this from the API key's project, so
# every query helper below (db/q) is tenant-scoped without touching any query code.
_cur_db = contextvars.ContextVar("pulse0_db", default=None)
_tenants = collections.OrderedDict()  # path -> (conn, lock); small LRU of open project databases
_tenants_guard = threading.Lock()
MAX_OPEN_TENANTS = int(os.environ.get("PULSE0_MAX_OPEN_PROJECTS", "64"))


def _handle():
    path = _cur_db.get()
    if path is None:
        return _conn, _lock
    with _tenants_guard:
        h = _tenants.get(path)
        if h is None:
            h = _tenants[path] = (_open_db(path), threading.Lock())
        _tenants.move_to_end(path)
        while len(_tenants) > MAX_OPEN_TENANTS:
            _tenants.popitem(last=False)  # dropped refs are closed by GC once no thread holds them
        return h


class _PgCursor:
    """Thin wrapper so pg cursors quack like sqlite3 cursors for our helpers."""
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, args=()):
        return self._conn.execute(_pg_sql(sql), args)
    def executemany(self, sql, rows):
        cur = self._conn.cursor()
        cur.executemany(_pg_sql(sql), rows)
        return cur


@contextmanager
def db():
    if USE_PG:
        with _pg_pool.connection() as conn:
            yield _PgCursor(conn)
            conn.commit()
    else:
        conn, lock = _handle()
        with lock:
            yield conn
            conn.commit()


_readers = threading.local()


def _reader():
    """Per-thread read connection to the default DB. WAL lets readers run alongside the single writer,
    so slow dashboard queries no longer queue behind (or block) ingest on the global lock."""
    c = getattr(_readers, "c", None)
    if c is None or getattr(_readers, "path", None) != DB_PATH:
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        c.row_factory = sqlite3.Row
        _readers.c, _readers.path = c, DB_PATH
    return c


def qrows(sql, args=()):
    """Raw row tuples (no dict conversion) for large scans aggregated by the caller."""
    if USE_PG:
        with _pg_pool.connection() as conn:
            return conn.execute(_pg_sql(sql), args).fetchall()
    if _cur_db.get() is None:
        return _reader().execute(sql, args).fetchall()
    conn, lock = _handle()
    with lock:
        return conn.execute(sql, args).fetchall()


def qcol(sql, args=()):
    """First column of every row as a plain list (no per-row dicts: much cheaper for big scans)."""
    if USE_PG:
        with _pg_pool.connection() as conn:
            return [r[0] for r in conn.execute(_pg_sql(sql), args)]
    if _cur_db.get() is None:
        return [r[0] for r in _reader().execute(sql, args)]
    conn, lock = _handle()
    with lock:
        return [r[0] for r in conn.execute(sql, args)]


# ---------------------------------------------------------------- heavy-read cache
# Aggregates over the whole time window (services, map, insights, home) cost seconds at ~1M spans. Concurrent callers share one
# computation (single-flight) and results live for CACHE_TTL seconds; a background warmer refreshes them while the UI is in use.
CACHE_TTL = float(os.environ.get("PULSE0_CACHE_TTL", "20"))
_last_use = [0.0]
_warming = threading.local()


def ttl_cached(fn):
    import functools, inspect
    sig = inspect.signature(fn)
    cache, locks = {}, {}

    def key_of(a, kw):
        b = sig.bind(*a, **kw); b.apply_defaults()
        return (_cur_db.get(), tuple(sorted(b.arguments.items())))

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if CACHE_TTL <= 0:
            return fn(*a, **kw)
        if not getattr(_warming, "on", False):
            _last_use[0] = time.time()
        k = key_of(a, kw)
        hit = cache.get(k)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        with locks.setdefault(k, threading.Lock()):
            hit = cache.get(k)
            if hit and time.time() - hit[0] < CACHE_TTL:
                return hit[1]
            v = fn(*a, **kw)
            if len(cache) > 256:
                cache.pop(min(cache, key=lambda x: cache[x][0]), None)
            cache[k] = (time.time(), v)
            return v

    def refresh(*a, **kw):
        k = key_of(a, kw)
        with locks.setdefault(k, threading.Lock()):
            cache[k] = (time.time(), fn(*a, **kw))

    wrapper.refresh = refresh
    return wrapper


def q(sql, args=()):
    if USE_PG:
        with _pg_pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                return cur.execute(_pg_sql(sql), args).fetchall()
    if _cur_db.get() is None and sql.lstrip().upper().startswith(("SELECT", "WITH")):
        return [dict(r) for r in _reader().execute(sql, args).fetchall()]
    conn, lock = _handle()
    with lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- OTLP decoding
def _val(v):
    """OTLP AnyValue (JSON form) -> python."""
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_val(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return _kv(v["kvlistValue"].get("values", []))
    return None


def _kv(lst):
    return {a["key"]: _val(a.get("value", {})) for a in (lst or [])}


def _res(rs):
    r = _kv(rs.get("resource", {}).get("attributes", []))
    return r.get("service.name", "unknown"), r


def _ns(x):
    return int(x) if x not in (None, "") else 0


def _hex(x):
    # JSON OTLP uses hex strings; protobuf->dict gives base64, handled in _to_json
    return x or ""


def _body_json(raw: bytes, ctype: str, msg_cls):
    if "protobuf" in ctype:
        from google.protobuf.json_format import MessageToDict
        m = msg_cls()
        m.ParseFromString(raw)
        d = MessageToDict(m, preserving_proto_field_name=False)
        return _fix_ids(d)
    return json.loads(raw or b"{}")


def _fix_ids(o):
    """MessageToDict base64-encodes bytes; convert trace/span ids to hex."""
    import base64
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k in ("traceId", "spanId", "parentSpanId") and isinstance(v, str):
                try:
                    o[k] = base64.b64decode(v).hex()
                except Exception:
                    pass
            else:
                o[k] = _fix_ids(v)
    elif isinstance(o, list):
        return [_fix_ids(x) for x in o]
    return o


_known_services: set = set()
_known_services_ts: float = 0

def _check_service_limit(svc_names: set):
    """On community tier, reject ingest if it would exceed the service cap."""
    if IS_PRO or not svc_names:
        return
    global _known_services, _known_services_ts
    now = time.time()
    if now - _known_services_ts > 60:
        rows = q("SELECT DISTINCT service FROM spans")
        _known_services = {r["service"] for r in rows}
        _known_services_ts = now
    new = svc_names - _known_services
    if new and len(_known_services | svc_names) > TIER_LIMITS["max_services"]:
        raise HTTPException(
            403,
            {"error": "tier_limit",
             "message": f"Community tier allows up to {TIER_LIMITS['max_services']} services. Upgrade to Pro for unlimited.",
             "limit": TIER_LIMITS["max_services"],
             "current_services": sorted(_known_services),
             "rejected_services": sorted(new),
             "tier": "community",
             "upgrade_url": "https://pulse0.dev/pricing"})


def ingest_traces(d):
    rows = []
    batch_services = set()
    for rs in d.get("resourceSpans", []):
        svc, res = _res(rs)
        batch_services.add(svc)
        rj = json.dumps(res)
        for ss in rs.get("scopeSpans", []):
            for s in ss.get("spans", []):
                st, en = _ns(s.get("startTimeUnixNano")), _ns(s.get("endTimeUnixNano"))
                status = s.get("status", {})
                rows.append((s.get("traceId", ""), s.get("spanId", ""), s.get("parentSpanId", ""), svc,
                             s.get("name", ""), int(s.get("kind", 0) if not isinstance(s.get("kind"), str) else
                                                    {"SPAN_KIND_SERVER": 2, "SPAN_KIND_CLIENT": 3, "SPAN_KIND_PRODUCER": 4,
                                                     "SPAN_KIND_CONSUMER": 5, "SPAN_KIND_INTERNAL": 1}.get(s["kind"], 0)),
                             st, en, (en - st) / 1e6,
                             {"STATUS_CODE_ERROR": 2, "STATUS_CODE_OK": 1}.get(status.get("code"), status.get("code", 0))
                             if isinstance(status.get("code", 0), str) else status.get("code", 0),
                             status.get("message", ""), json.dumps(_kv(s.get("attributes"))), rj))
    _check_service_limit(batch_services)
    with db() as c:
        c.executemany("INSERT INTO spans(trace_id,span_id,parent_id,service,name,kind,start_ns,end_ns,dur_ms,status,status_msg,attrs,res) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    _known_services.update(batch_services)
    return len(rows)


_SEV = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}


def ingest_logs(d):
    rows = []
    for rl in d.get("resourceLogs", []):
        svc, res = _res(rl)
        if svc == "unknown" and res.get("k8s.container.name"):
            svc = res["k8s.container.name"]  # filelog container logs carry no service.name
        rj = json.dumps(res)
        for sl in rl.get("scopeLogs", []):
            for l in sl.get("logRecords", []):
                sev = l.get("severityNumber", 0)
                if isinstance(sev, str):
                    sev = _SEV.get(sev.replace("SEVERITY_NUMBER_", "").split("_")[0], 0)
                body = _val(l.get("body", {}))
                rows.append((_ns(l.get("timeUnixNano") or l.get("observedTimeUnixNano")) or time.time_ns(), svc, int(sev),
                             l.get("severityText", ""), body if isinstance(body, str) else json.dumps(body),
                             l.get("traceId", ""), l.get("spanId", ""), json.dumps(_kv(l.get("attributes"))), rj))
    with db() as c:
        c.executemany("INSERT INTO logs(ts_ns,service,severity,sev_text,body,trace_id,span_id,attrs,res) VALUES(?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def ingest_metrics(d):
    rows = []
    for rm in d.get("resourceMetrics", []):
        svc, res = _res(rm)
        rj = json.dumps(res)
        for sm in rm.get("scopeMetrics", []):
            for m in sm.get("metrics", []):
                name, unit = m.get("name", ""), m.get("unit", "")
                for mtype in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
                    if mtype not in m:
                        continue
                    for p in m[mtype].get("dataPoints", []):
                        if mtype in ("histogram", "exponentialHistogram", "summary"):
                            cnt = float(p.get("count", 0) or 0)
                            tot = float(p.get("sum", 0) or 0)
                            val = tot / cnt if cnt else 0.0  # mean of the interval
                        else:
                            val = float(p["asDouble"]) if "asDouble" in p else float(p.get("asInt", 0))
                        rows.append((_ns(p.get("timeUnixNano")) or time.time_ns(), svc, name, unit, mtype, val,
                                     json.dumps(_kv(p.get("attributes"))), rj))
    with db() as c:
        c.executemany("INSERT INTO metrics(ts_ns,service,name,unit,mtype,value,attrs,res) VALUES(?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# ---------------------------------------------------------------- validation models
SIGNALS = {"error_rate", "p95_latency", "request_rate", "log_errors", "metric_avg", "metric_max", "synthetic_down"}
OPS_SET = {">", ">=", "<", "<="}


class AlertRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    signal: Literal["error_rate", "p95_latency", "request_rate", "log_errors", "metric_avg", "metric_max", "synthetic_down"]
    expr: str = Field(default="", max_length=512)
    op: Literal[">", ">=", "<", "<="]
    threshold: float = Field(ge=-1e12, le=1e12)
    window_s: int = Field(default=300, ge=15, le=86400)
    service: str = Field(default="", max_length=128)
    webhook: str = Field(default="", max_length=2048)

    @field_validator("webhook")
    @classmethod
    def _wh(cls, v):
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("webhook must be http(s) URL")
        return v


class SyntheticIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=2048)
    interval_s: int = Field(default=60, ge=10, le=3600)
    expect_status: int = Field(default=200, ge=100, le=599)
    max_ms: float = Field(default=2000, ge=1, le=60000)

    @field_validator("url")
    @classmethod
    def _u(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class DashboardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    panels: list = Field(default_factory=list, max_length=50)


class SloIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    service: str = Field(min_length=1, max_length=128)
    objective_pct: float = Field(ge=50.0, le=100.0)
    latency_ms: float = Field(ge=1, le=60000)
    window_days: int = Field(default=30, ge=1, le=90)


# ---------------------------------------------------------------- app
app = FastAPI(title="Pulse0", docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def request_id(request: Request, call_next):
    """Attach a stable request id to every request + response.
    Must NOT catch exceptions - FastAPI's own handlers (validation, HTTPException)
    need to run first to produce proper 4xx responses."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.rid = rid
    resp = await call_next(request)
    resp.headers["x-request-id"] = rid
    return resp


@app.exception_handler(RequestValidationError)
async def _vexc(request: Request, exc: RequestValidationError):
    # Pydantic puts raw Exception objects in err["ctx"]; stringify so JSON works.
    details = []
    for err in exc.errors():
        clean = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            clean["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        details.append(clean)
    return JSONResponse({"error": "validation_failed", "details": details,
                         "request_id": getattr(request.state, "rid", "")}, status_code=400)


@app.exception_handler(HTTPException)
async def _hexc(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        body = {**exc.detail, "status": exc.status_code, "request_id": getattr(request.state, "rid", "")}
    else:
        body = {"error": "http_error", "message": exc.detail, "status": exc.status_code, "request_id": getattr(request.state, "rid", "")}
    return JSONResponse(body, status_code=exc.status_code)

from fastapi.middleware.cors import CORSMiddleware
# Allow browser (Angular/React/Vue) RUM to POST OTLP directly from any origin.
# navigator.sendBeacon always sends credentialed requests, and browsers reject a
# wildcard Allow-Origin for those, so echo the request origin instead. Auth is a
# bearer/ingest key header, never a cookie, so echoing the origin adds no CSRF risk.
app.add_middleware(CORSMiddleware, allow_origin_regex=".*", allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])


# ---------------------------------------------------------------- SSO (OIDC) + RBAC (self-hosted)
# Identity sources, checked in order: bearer PULSE0_TOKEN (admin, unchanged), optional PULSE0_READ_TOKEN (viewer), a signed
# session cookie issued after an OpenID Connect login, and - only when none of those is configured - open access (as before).
# Roles: viewer (read) < editor (create/change dashboards, alerts, SLOs, synthetics) < admin (also the audit log).
# /v1/* ingest is never session-authenticated: it needs PULSE0_TOKEN or PULSE0_INGEST_TOKEN when either is set.
ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _csv(name):
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def _load_oidc():
    cfg = dict(
        issuer=os.environ.get("PULSE0_OIDC_ISSUER", "").strip().rstrip("/"),
        client_id=os.environ.get("PULSE0_OIDC_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("PULSE0_OIDC_CLIENT_SECRET", ""),
        scopes=os.environ.get("PULSE0_OIDC_SCOPES", "openid email profile"),
        role_claim=os.environ.get("PULSE0_OIDC_ROLE_CLAIM", "groups").strip(),
        admin_groups=_csv("PULSE0_OIDC_ADMIN_GROUPS"), editor_groups=_csv("PULSE0_OIDC_EDITOR_GROUPS"),
        viewer_groups=_csv("PULSE0_OIDC_VIEWER_GROUPS"),
        default_role=os.environ.get("PULSE0_OIDC_DEFAULT_ROLE", "viewer").strip().lower(),
        allowed_domains=[d.lower().lstrip("@") for d in _csv("PULSE0_OIDC_ALLOWED_DOMAINS")],
        redirect_url=os.environ.get("PULSE0_OIDC_REDIRECT_URL", "").strip(),
        session_ttl_h=float(os.environ.get("PULSE0_SESSION_TTL_HOURS", "12")),
        provider_name=os.environ.get("PULSE0_OIDC_PROVIDER_NAME", "SSO").strip() or "SSO",
    )
    if cfg["default_role"] not in ROLE_RANK and cfg["default_role"] not in ("", "none", "deny"):
        raise RuntimeError("PULSE0_OIDC_DEFAULT_ROLE must be viewer, editor, admin or none")
    if cfg["default_role"] in ("none", "deny"):
        cfg["default_role"] = ""
    return cfg


OIDC = _load_oidc()
READ_TOKEN = os.environ.get("PULSE0_READ_TOKEN", "")
INGEST_TOKEN = os.environ.get("PULSE0_INGEST_TOKEN", "")
AUDIT_DAYS = float(os.environ.get("PULSE0_AUDIT_RETENTION_DAYS", "90"))
_SESSION_SECRET = (os.environ.get("PULSE0_SESSION_SECRET") or secrets.token_hex(32)).encode()


def sso_enabled():
    return not HOSTED and bool(OIDC["issuer"] and OIDC["client_id"])


if sso_enabled():
    if not os.environ.get("PULSE0_SESSION_SECRET"):
        print("warning: PULSE0_SESSION_SECRET is not set; SSO sessions will not survive a restart or span replicas")
    if not (AUTH_TOKEN or INGEST_TOKEN):
        print("warning: SSO is on but /v1/* ingest has no token; set PULSE0_TOKEN or PULSE0_INGEST_TOKEN")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(x):
    return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))


def _sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(_SESSION_SECRET, body.encode(), hashlib.sha256).digest())


def _unsign(tok):
    """Verify an HMAC-signed cookie value and return its payload, or None if forged, malformed or expired."""
    try:
        body, sig = (tok or "").split(".", 1)
        good = _b64(hmac.new(_SESSION_SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        d = json.loads(_unb64(body))
        return d if d.get("exp", 0) > time.time() else None
    except Exception:
        return None


def _required_role(method, path):
    if path == "/api/audit" or path.startswith("/api/audit/"):
        return "admin"
    if method in _SAFE_METHODS or path in _READ_ONLY_POSTS:
        return "viewer"
    return "editor"


def _tok_eq(given, want):
    return bool(want) and hmac.compare_digest(given.encode(), want.encode())


def _bearer(request):
    h = request.headers.get("authorization", "")
    return h[7:] if h.startswith("Bearer ") else ""


def _identify(request):
    t = _bearer(request)
    if t:
        if _tok_eq(t, AUTH_TOKEN):
            return dict(actor="api-token", role="admin", via="token")
        if _tok_eq(t, READ_TOKEN):
            return dict(actor="read-token", role="viewer", via="token")
    if sso_enabled():
        sess = _unsign(request.cookies.get("p0_session", ""))
        if sess and sess.get("role") in ROLE_RANK:
            return dict(actor=sess.get("email") or sess.get("sub", "?"), name=sess.get("name", ""), role=sess["role"], via="session")
    if not (AUTH_TOKEN or READ_TOKEN or sso_enabled()):
        return dict(actor="anonymous", role="admin", via="open")
    return None


def _audit(ident, method, path, status, detail=""):
    try:
        with db() as c:
            c.execute("INSERT INTO audit(ts,actor,role,via,method,path,status,detail) VALUES(?,?,?,?,?,?,?,?)",
                      (time.time_ns(), ident["actor"], ident["role"], ident["via"], method, path, int(status), detail[:300]))
    except Exception as e:
        print("audit error:", e)


_PUBLIC_API = ("/api/mode", "/api/auth/config")


@app.middleware("http")
async def auth(request: Request, call_next):
    if HOSTED:
        return await _hosted_auth(request, call_next)
    path, method = request.url.path, request.method
    if path.startswith("/v1/"):
        allowed = [t for t in (AUTH_TOKEN, INGEST_TOKEN) if t]
        if allowed and not any(_tok_eq(_bearer(request), t) for t in allowed):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
    if not path.startswith("/api/") or path in _PUBLIC_API or method == "OPTIONS":
        return await call_next(request)
    ident = _identify(request)
    if ident is None:
        return JSONResponse({"error": "unauthorized", "sso": sso_enabled()}, status_code=401)
    need = _required_role(method, path)
    if ROLE_RANK[ident["role"]] < ROLE_RANK[need]:
        return JSONResponse({"error": "forbidden", "message": f"this action requires the {need} role (you are {ident['role']})",
                             "status": 403}, status_code=403)
    if ident["via"] == "session" and method not in _SAFE_METHODS:
        origin = request.headers.get("origin")  # cookie-authenticated writes must come from this site (CSRF)
        if origin and urllib.parse.urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"error": "forbidden", "message": "cross-origin request refused", "status": 403}, status_code=403)
    request.state.who = ident
    resp = await call_next(request)
    if method not in _SAFE_METHODS:
        await run_in_threadpool(_audit, ident, method, path, resp.status_code)
    return resp


@app.middleware("http")
async def no_cache_ui(request: Request, call_next):
    """UI assets are tiny and change on deploy; always revalidate so users never see a stale UI."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


async def _otlp(request: Request, name, mod, fn):
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if request.headers.get("content-encoding") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    try:
        if "protobuf" in ctype:
            import importlib
            p = importlib.import_module(mod)
            d = _body_json(raw, ctype, getattr(p, name))
        else:
            d = json.loads(raw or b"{}")
        n = await run_in_threadpool(fn, d)  # JSON decode + SQLite insert must not block the event loop
    except Exception as e:
        raise HTTPException(400, f"bad OTLP payload: {e}")
    if HOSTED:
        _usage_add(getattr(request.state, "ident", None), fn.__name__, n or 0)
    return {"partialSuccess": {}}


@app.post("/v1/traces")
async def v1_traces(r: Request):
    return await _otlp(r, "ExportTraceServiceRequest", "opentelemetry.proto.collector.trace.v1.trace_service_pb2", ingest_traces)


@app.post("/v1/logs")
async def v1_logs(r: Request):
    return await _otlp(r, "ExportLogsServiceRequest", "opentelemetry.proto.collector.logs.v1.logs_service_pb2", ingest_logs)


@app.post("/v1/metrics")
async def v1_metrics(r: Request):
    return await _otlp(r, "ExportMetricsServiceRequest", "opentelemetry.proto.collector.metrics.v1.metrics_service_pb2", ingest_metrics)


@app.get("/healthz")
async def healthz():
    """Liveness: process is up and serving."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: database is reachable. A busy writer is not "not ready"."""
    try:
        await run_in_threadpool(q, "SELECT 1")
        return {"status": "ready", "retention_hours": RETENTION_HOURS}
    except Exception as e:
        raise HTTPException(503, f"not ready: {e}")


@app.get("/api/stats")
@ttl_cached
def stats():
    """Operational stats for monitoring Pulse0 itself."""
    return {
        "spans": q("SELECT COUNT(*) n FROM spans")[0]["n"],
        "logs": q("SELECT COUNT(*) n FROM logs")[0]["n"],
        "metrics": q("SELECT COUNT(*) n FROM metrics")[0]["n"],
        "services": q("SELECT COUNT(DISTINCT service) n FROM spans")[0]["n"],
        "alert_rules": q("SELECT COUNT(*) n FROM alert_rules")[0]["n"],
        "synthetics": q("SELECT COUNT(*) n FROM synthetics")[0]["n"],
        "retention_hours": RETENTION_HOURS,
        "db_path": DB_PATH if not HOSTED else "hosted",
    }


def since_ns(minutes):
    return time.time_ns() - int(float(minutes) * 60 * 1e9)


# ---- resources / services (RED)
@app.get("/api/services")
@ttl_cached
def services(minutes: float = 60):
    t0 = since_ns(minutes)
    # One scan: counts, errors, avg/max and percentiles per service, aggregated in Python from plain tuples.
    per = {}
    for svc, st, dur in qrows("SELECT service, status, dur_ms FROM spans WHERE start_ns>=? AND (parent_id='' OR kind=2)", (t0,)):
        a = per.get(svc)
        if a is None:
            a = per[svc] = [0, 0, 0.0, 0.0, []]
        a[0] += 1; a[1] += st == 2; a[2] += dur; a[3] = max(a[3], dur); a[4].append(dur)
    rows = []
    for svc, (n, err, tot, mx, d) in sorted(per.items(), key=lambda kv: -kv[1][0]):
        d.sort()
        rows.append(dict(service=svc, n=n, err=err, avg_ms=tot / n, max_ms=mx,
                         p50=d[int(n * .5)], p95=d[min(n - 1, int(n * .95))], p99=d[min(n - 1, int(n * .99))]))
    for r in rows:
        r["rate"] = r["n"] / (minutes * 60)
        r["err_pct"] = 100 * (r["err"] or 0) / r["n"] if r["n"] else 0
        r["err"] = r["err"] or 0
    # services seen only via logs/metrics
    known = {r["service"] for r in rows}
    for t in ("logs", "metrics"):
        for x in q(f"SELECT DISTINCT service FROM {t} WHERE ts_ns>=?", (t0,)):
            if x["service"] not in known:
                known.add(x["service"])
                rows.append(dict(service=x["service"], n=0, err=0, err_pct=0, avg_ms=0, max_ms=0, p50=0, p95=0, p99=0, rate=0))
    return rows


@app.get("/api/services/{svc}/timeseries")
def service_ts(svc: str, minutes: float = 60, buckets: int = Query(60, ge=1, le=1000)):
    buckets = max(1, min(int(buckets), 1000))
    t0 = since_ns(minutes)
    w = int(minutes * 60 * 1e9 / buckets)
    rows = q("""SELECT (start_ns-?)/? b, COUNT(*) n, SUM(status=2) err, AVG(dur_ms) avg_ms
                FROM spans WHERE service=? AND start_ns>=? AND (parent_id='' OR kind=2) GROUP BY b""", (t0, w, svc, t0))
    by = {r["b"]: r for r in rows}
    out = []
    for i in range(buckets):
        r = by.get(i)
        out.append(dict(t=(t0 + i * w) / 1e6, rate=(r["n"] / (w / 1e9)) if r else 0, err=(r["err"] or 0) / (w / 1e9) if r else 0,
                        avg_ms=r["avg_ms"] if r else 0))
    return out


@app.get("/api/resources/{svc}")
def resource_attrs(svc: str):
    for t in ("spans", "logs", "metrics"):
        r = q(f"SELECT res FROM {t} WHERE service=? LIMIT 1", (svc,))
        if r:
            return _jload(r[0]["res"])
    return {}


@app.get("/api/operations")
def operations(service: str, minutes: float = 60):
    t0 = since_ns(minutes)
    return q("""SELECT name, COUNT(*) n, SUM(status=2) err, AVG(dur_ms) avg_ms, MAX(dur_ms) max_ms
                FROM spans WHERE service=? AND start_ns>=? AND (kind IN (2,5) OR parent_id='') GROUP BY name ORDER BY n DESC LIMIT 100""",
             (service, t0))


# ---- service map
@app.get("/api/map")
@ttl_cached
def service_map(minutes: float = 60):
    t0 = since_ns(minutes)
    edges = q("""SELECT p.service src, c.service dst, COUNT(*) calls, SUM(c.status=2) err, AVG(c.dur_ms) avg_ms
                 FROM spans c JOIN spans p ON c.trace_id=p.trace_id AND c.parent_id=p.span_id
                 WHERE c.start_ns>=? AND p.service<>c.service GROUP BY src,dst""", (t0,))
    # outbound calls to uninstrumented backends (databases, queues, named peers).
    # Use explicit semantic attributes only (peer.service / db.system / messaging.system);
    # we deliberately skip raw server.address so we don't create noisy bare-IP nodes
    # that duplicate an already-instrumented service.
    _dst_expr = """COALESCE(json_extract(attrs,'$."peer.service"'),
                               json_extract(attrs,'$."db.system"'),
                               json_extract(attrs,'$."messaging.system"'))"""
    ext = q(f"""SELECT service src, {_dst_expr} dst,
                      COUNT(*) calls, SUM(status=2) err, AVG(dur_ms) avg_ms
               FROM spans WHERE kind=3 AND start_ns>=? GROUP BY src,dst HAVING {_dst_expr} IS NOT NULL""", (t0,))
    svc_names = {s["service"] for s in q("SELECT DISTINCT service FROM spans")}
    seen = {(e["src"], e["dst"]) for e in edges}
    for e in ext:
        # don't duplicate an edge we already derived from span parent/child,
        # and don't add a node that is actually an instrumented service
        if e["dst"] in svc_names or (e["src"], e["dst"]) in seen:
            continue
        edges.append(e)
    return edges


@app.get("/api/map/nodes")
def map_nodes(minutes: float = 60):
    """Per-service resource metadata for the service map (language badge, namespace, version).
    Kept separate from /api/map so that endpoint's contract (a bare edge list) is unchanged."""
    t0 = since_ns(minutes)
    out = []
    for r in q("SELECT service, MIN(res) res FROM spans WHERE start_ns>=? GROUP BY service", (t0,)):
        try:
            a = _jload(r["res"])
        except ValueError:
            a = {}
        a = a.get("attributes", a) if isinstance(a, dict) else {}
        out.append({"service": r["service"],
                    "language": a.get("telemetry.sdk.language", ""),
                    "namespace": a.get("k8s.namespace.name") or a.get("service.namespace") or "",
                    "version": a.get("service.version", ""),
                    "env": a.get("deployment.environment", ""),
                    "runtime": a.get("process.runtime.name", "")})
    return out


# ---- traces
@app.get("/api/traces")
def traces(minutes: float = 60, service: str = "", errors: bool = False, min_ms: float = 0, q_: str = "", limit: int = 100):
    t0 = since_ns(minutes)
    where, args = ["start_ns>=?"], [t0]
    if service:
        where.append("service=?"); args.append(service)
    if q_:
        where.append("(name LIKE ? OR attrs LIKE ? OR trace_id LIKE ?)"); args += [f"%{q_}%"] * 3
    # Candidate traces = those with a span among the newest spans in the window (index scan on start_ns),
    # so listing 50 traces does not aggregate every trace in the window.
    cap = max(limit * 50, 5000)
    cand = list(where) + (["status=2"] if errors else [])
    sub = f"SELECT DISTINCT trace_id FROM (SELECT trace_id FROM spans WHERE {' AND '.join(cand)} ORDER BY start_ns DESC LIMIT {int(cap)})"
    having = []
    if errors:
        having.append("SUM(status=2)>0")
    if min_ms:
        having.append(f"(MAX(end_ns)-MIN(start_ns))/1e6>={float(min_ms)}")
    h = ("HAVING " + " AND ".join(having)) if having else ""
    return q(f"""SELECT trace_id, MIN(start_ns) start_ns, (MAX(end_ns)-MIN(start_ns))/1e6 dur_ms, COUNT(*) spans,
                 SUM(status=2) errors, COUNT(DISTINCT service) services,
                 (SELECT name FROM spans s2 WHERE s2.trace_id=s.trace_id ORDER BY (parent_id<>''), start_ns LIMIT 1) root,
                 (SELECT service FROM spans s2 WHERE s2.trace_id=s.trace_id ORDER BY (parent_id<>''), start_ns LIMIT 1) root_service
                 FROM spans s WHERE trace_id IN ({sub}) GROUP BY trace_id {h} ORDER BY start_ns DESC LIMIT ?""", (*args, limit))


@app.get("/api/traces/{trace_id}")
def trace(trace_id: str):
    spans = q("SELECT * FROM spans WHERE trace_id=? ORDER BY start_ns", (trace_id,))
    if not spans:
        raise HTTPException(404, "trace not found")
    for s in spans:
        s["attrs"] = _jload(s["attrs"])
        s.pop("res", None)
    logs = q("SELECT ts_ns, service, sev_text, severity, body, span_id FROM logs WHERE trace_id=? ORDER BY ts_ns", (trace_id,))
    return {"spans": spans, "logs": logs}


# ---- logs
@app.get("/api/logs")
def logs(minutes: float = 60, service: str = "", min_severity: int = 0, q_: str = "", trace_id: str = "", limit: int = 300):
    where, args = ["ts_ns>=?"], [since_ns(minutes)]
    if service:
        where.append("service=?"); args.append(service)
    if min_severity:
        where.append("severity>=?"); args.append(min_severity)
    if trace_id:
        where.append("trace_id=?"); args.append(trace_id)
    if q_:
        # supports `key=value` attribute filters and free text
        for tok in q_.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                # Cast to text so numeric attrs (e.g. http.status_code=500) match too
                where.append("(CAST(json_extract(attrs, ?) AS TEXT)=? OR CAST(json_extract(res, ?) AS TEXT)=?)")
                args += [f'$."{k}"', v, f'$."{k}"', v]
            else:
                where.append("body LIKE ?"); args.append(f"%{tok}%")
    rows = q(f"SELECT * FROM logs WHERE {' AND '.join(where)} ORDER BY ts_ns DESC LIMIT ?", (*args, limit))
    for r in rows:
        r["attrs"] = _jload(r["attrs"]); r.pop("res", None)
    return rows


@app.get("/api/logs/histogram")
def logs_hist(minutes: float = 60, service: str = "", buckets: int = Query(60, ge=1, le=1000)):
    buckets = max(1, min(int(buckets), 1000))
    t0 = since_ns(minutes); w = int(minutes * 60 * 1e9 / buckets)
    where, args = ["ts_ns>=?"], [t0]
    if service:
        where.append("service=?"); args.append(service)
    rows = q(f"SELECT (ts_ns-?)/? b, COUNT(*) n, SUM(severity>=17) err, SUM(severity>=13 AND severity<17) warn FROM logs WHERE {' AND '.join(where)} GROUP BY b", (t0, w, *args))
    by = {r["b"]: r for r in rows}
    return [dict(t=(t0 + i * w) / 1e6, n=by.get(i, {}).get("n", 0), err=by.get(i, {}).get("err") or 0, warn=by.get(i, {}).get("warn") or 0) for i in range(buckets)]


# ---- metrics
@app.get("/api/metrics/names")
def metric_names():
    _gc = "STRING_AGG(DISTINCT service, ',')" if USE_PG else "GROUP_CONCAT(DISTINCT service)"
    _gb = "name, unit, mtype" if USE_PG else "name"
    return q(f"SELECT name, unit, mtype, COUNT(*) points, {_gc} services FROM metrics GROUP BY {_gb} ORDER BY name")


def _metric_series(name, service, minutes, buckets, agg, group_by=""):
    buckets = max(1, min(int(buckets), 1000))
    t0 = since_ns(minutes); w = int(minutes * 60 * 1e9 / buckets)
    fn = {"avg": "AVG", "max": "MAX", "min": "MIN", "sum": "SUM", "count": "COUNT"}.get(agg, "AVG")
    where, args = ["name=?", "ts_ns>=?"], [name, t0]
    if service:
        where.append("service=?"); args.append(service)
    if group_by:
        g = ", json_extract(attrs, ?) grp"
        g_args = (f'$."{group_by}"',)
    else:
        g = ", service grp"
        g_args = ()
    rows = q(f"SELECT (ts_ns-?)/? b{g}, {fn}(value) v FROM metrics WHERE {' AND '.join(where)} GROUP BY b, grp", (t0, w, *g_args, *args))
    series = {}
    for r in rows:
        series.setdefault(str(r["grp"]), {})[r["b"]] = r["v"]
    return {"t": [(t0 + i * w) / 1e6 for i in range(buckets)],
            "series": {k: [v.get(i) for i in range(buckets)] for k, v in series.items()}}


@app.get("/api/metrics/query")
def metric_query(name: str, service: str = "", minutes: float = 60, buckets: int = Query(60, ge=1, le=1000), agg: str = "avg", group_by: str = ""):
    return _metric_series(name, service, minutes, buckets, agg, group_by)


# ---- service health score (unique differentiator vs Dash0)
# Combines error rate, p95 latency drift, log errors and synthetic health into
# a 0-100 score per service with a one-line reason. Pulse0 computes this from
# raw signals; no rules required.
@app.get("/api/services/{svc}/health")
def service_health(svc: str, minutes: float = 60):
    t0 = since_ns(minutes)
    row = q("""SELECT COUNT(*) n, SUM(status=2) err, AVG(dur_ms) avg_ms FROM spans
               WHERE service=? AND start_ns>=? AND (parent_id='' OR kind=2)""", (svc, t0))[0]
    if not row["n"]:
        return {"service": svc, "score": None, "reason": "no traffic in window"}
    err_pct = 100 * (row["err"] or 0) / row["n"]
    dur = [x["dur_ms"] for x in q("""SELECT dur_ms FROM spans WHERE service=? AND start_ns>=?
                                     AND (parent_id='' OR kind=2) ORDER BY dur_ms""", (svc, t0))]
    p95 = dur[min(len(dur) - 1, int(len(dur) * .95))] if dur else 0
    # previous window baseline
    t_prev = t0 - int(float(minutes) * 60 * 1e9)
    dur_prev = [x["dur_ms"] for x in q("""SELECT dur_ms FROM spans WHERE service=? AND start_ns>=? AND start_ns<?
                                          AND (parent_id='' OR kind=2) ORDER BY dur_ms""", (svc, t_prev, t0))]
    p95_prev = dur_prev[min(len(dur_prev) - 1, int(len(dur_prev) * .95))] if dur_prev else p95
    drift = (p95 - p95_prev) / p95_prev if p95_prev else 0
    log_err_rate = q("SELECT COUNT(*) n FROM logs WHERE service=? AND ts_ns>=? AND severity>=17",
                     (svc, t0))[0]["n"] / max(1, minutes)
    # score components (each contributes up to the listed weight)
    score = 100.0
    reasons = []
    if err_pct > 1:
        p = min(40, err_pct * 4); score -= p
        reasons.append(f"error rate {err_pct:.1f}%")
    if drift > 0.2:
        p = min(25, drift * 50); score -= p
        reasons.append(f"p95 latency up {drift * 100:.0f}% vs prior window")
    if log_err_rate > 5:
        p = min(20, log_err_rate); score -= p
        reasons.append(f"{log_err_rate:.0f} error logs/min")
    syn_down = q("""SELECT COUNT(*) n FROM synthetic_runs
                    WHERE ts>=? AND ok=0""", (int(time.time() - minutes * 60),))[0]["n"]
    if syn_down > 0:
        score -= min(15, syn_down * 3)
        reasons.append(f"{syn_down} failed synthetic checks")
    score = max(0, round(score, 1))
    state = "healthy" if score >= 90 else "degraded" if score >= 70 else "unhealthy"
    return {"service": svc, "score": score, "state": state, "reason": "; ".join(reasons) or "all green",
            "signals": {"error_pct": round(err_pct, 2), "p95_ms": round(p95, 1),
                        "p95_drift_pct": round(drift * 100, 1), "log_err_per_min": round(log_err_rate, 1)}}


# ---- SLOs (service-level objectives) -- feature gap vs dashboards-only competitors
@app.get("/api/slos")
def slos_list():
    rows = q("SELECT * FROM slos")
    for r in rows:
        t0 = time.time_ns() - int(r["window_days"] * 86400 * 1e9)
        tot = q("SELECT COUNT(*) n, SUM(status=2) err FROM spans WHERE service=? AND start_ns>=? AND (parent_id='' OR kind=2)",
                (r["service"], t0))[0]
        d = [x["dur_ms"] for x in q("SELECT dur_ms FROM spans WHERE service=? AND start_ns>=? AND (parent_id='' OR kind=2)", (r["service"], t0))]
        good = sum(1 for x in d if x <= r["latency_ms"]) - (tot["err"] or 0)
        good = max(0, good)
        total = tot["n"] or 0
        attainment = 100 * good / total if total else None
        budget_used = max(0.0, (100 - attainment) / (100 - r["objective_pct"]) * 100) if attainment is not None and r["objective_pct"] < 100 else None  # share of the allowed failures already used
        r["attainment_pct"] = round(attainment, 3) if attainment is not None else None
        r["error_budget_used_pct"] = round(budget_used, 1) if budget_used is not None else None
        r["events"] = total
    return rows


@app.post("/api/slos")
async def slo_create(body: SloIn):
    _tier_check("slos", "slos")
    try:
        with db() as c:
            c.execute("INSERT INTO slos(name,service,objective_pct,latency_ms,window_days) VALUES(?,?,?,?,?)",
                      (body.name, body.service, body.objective_pct, body.latency_ms, body.window_days))
    except _IntegrityError:
        raise HTTPException(409, f"SLO named '{body.name}' already exists")
    return {"ok": True}


@app.delete("/api/slos/{sid}")
def slo_delete(sid: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM slos WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "SLO not found")
        c.execute("DELETE FROM slos WHERE id=?", (sid,))
    return {"ok": True}


# ---- custom dashboards (saved panels on top of metric queries)
@app.get("/api/dashboards")
def dashboards_list():
    rows = q("SELECT * FROM dashboards ORDER BY name")
    for r in rows:
        r["panels"] = json.loads(r["panels"] or "[]")
    return rows


@app.post("/api/dashboards")
async def dashboard_save(body: DashboardIn):
    existing = q("SELECT id FROM dashboards WHERE name=?", (body.name,))
    if not existing:
        _tier_check("dashboards", "dashboards")
    with db() as c:
        c.execute("""INSERT INTO dashboards(name,panels,updated) VALUES(?,?,?)
                     ON CONFLICT(name) DO UPDATE SET panels=excluded.panels, updated=excluded.updated""",
                  (body.name, json.dumps(body.panels), int(time.time())))
    return {"ok": True}


@app.delete("/api/dashboards/{did}")
def dashboard_delete(did: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM dashboards WHERE id=?", (did,)).fetchone():
            raise HTTPException(404, "dashboard not found")
        c.execute("DELETE FROM dashboards WHERE id=?", (did,))
    return {"ok": True}


# ---- alerts


def eval_signal(signal, expr, service, window_s):
    t0 = time.time_ns() - int(window_s * 1e9)
    sv, sa = (" AND service=?", [service]) if service else ("", [])
    if signal == "error_rate":
        r = q(f"SELECT COUNT(*) n, SUM(status=2) e FROM spans WHERE start_ns>=? AND (parent_id='' OR kind=2){sv}", (t0, *sa))[0]
        return (100.0 * (r["e"] or 0) / r["n"]) if r["n"] else 0.0
    if signal == "request_rate":
        r = q(f"SELECT COUNT(*) n FROM spans WHERE start_ns>=? AND (parent_id='' OR kind=2){sv}", (t0, *sa))[0]
        return r["n"] / window_s
    if signal == "p95_latency":
        d = [x["dur_ms"] for x in q(f"SELECT dur_ms FROM spans WHERE start_ns>=? AND (parent_id='' OR kind=2){sv} ORDER BY dur_ms", (t0, *sa))]
        return d[min(len(d) - 1, int(len(d) * .95))] if d else 0.0
    if signal == "log_errors":
        return float(q(f"SELECT COUNT(*) n FROM logs WHERE ts_ns>=? AND severity>=17{sv}" + (" AND body LIKE ?" if expr else ""),
                       (t0, *sa, *([f"%{expr}%"] if expr else [])))[0]["n"])
    if signal in ("metric_avg", "metric_max"):
        fn = "AVG" if signal == "metric_avg" else "MAX"
        r = q(f"SELECT {fn}(value) v FROM metrics WHERE name=? AND ts_ns>=?{sv}", (expr, t0, *sa))[0]
        return r["v"] if r["v"] is not None else 0.0
    if signal == "synthetic_down":
        r = q("SELECT COUNT(*) n FROM synthetic_runs WHERE ok=0 AND ts>=?" + (" AND check_id=?" if expr else ""),
              (int(time.time()) - window_s, *([int(expr)] if expr else [])))[0]
        return float(r["n"])
    return 0.0


OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def notify(url, payload):
    if not url:
        return
    try:
        blocked = _blocked_dest(url)
        if blocked:
            print("webhook blocked:", blocked)
            return
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        _urlopen(req, timeout=8).read()
    except Exception as e:
        print("webhook failed:", e)


def evaluate_rules():
    for r in q("SELECT * FROM alert_rules WHERE enabled=1"):
        v = eval_signal(r["signal"], r["expr"], r["service"], r["window_s"])
        firing = OPS.get(r["op"], OPS[">"])(v, r["threshold"])
        new = "firing" if firing else "ok"
        now = int(time.time())
        with db() as c:
            c.execute("UPDATE alert_rules SET last_value=?, last_eval=?, state=?, since=CASE WHEN state<>? THEN ? ELSE since END WHERE id=?",
                      (v, now, new, new, now, r["id"]))
            if new != r["state"]:
                msg = f"{r['name']}: {r['signal']} {v:.2f} {r['op']} {r['threshold']}" if firing else f"{r['name']} recovered ({v:.2f})"
                c.execute("INSERT INTO alert_events(rule_id,ts,state,value,msg) VALUES(?,?,?,?,?)", (r["id"], now, new, v, msg))
                threading.Thread(target=notify, args=(r["webhook"], {"text": f"[Pulse0] {new.upper()} - {msg}", "state": new, "rule": r["name"], "value": v}), daemon=True).start()


@app.get("/api/alerts")
def alerts():
    return q("SELECT * FROM alert_rules ORDER BY id")


@app.post("/api/alerts")
async def alert_create(body: AlertRuleIn):
    _tier_check("alerts", "alert_rules")
    with db() as c:
        c.execute("INSERT INTO alert_rules(name,signal,expr,op,threshold,window_s,service,webhook) VALUES(?,?,?,?,?,?,?,?)",
                  (body.name, body.signal, body.expr, body.op, body.threshold,
                   body.window_s, body.service, body.webhook))
    return {"ok": True}


@app.delete("/api/alerts/{rid}")
def alert_delete(rid: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM alert_rules WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404, "alert rule not found")
        c.execute("DELETE FROM alert_rules WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/api/alerts/{rid}/toggle")
def alert_toggle(rid: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM alert_rules WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404, "alert rule not found")
        c.execute("UPDATE alert_rules SET enabled=1-enabled WHERE id=?", (rid,))
    return {"ok": True}


@app.get("/api/alert-events")
def alert_events():
    return q("SELECT e.*, r.name FROM alert_events e LEFT JOIN alert_rules r ON r.id=e.rule_id ORDER BY e.id DESC LIMIT 100")


# ---- synthetics
def run_check(ch):
    t = time.time(); ok, status, err, ssl_days = 0, 0, "", None
    blocked = _blocked_dest(ch["url"])
    if blocked:
        with db() as c:
            c.execute("INSERT INTO synthetic_runs(check_id,ts,ok,status,ms,ssl_days,err) VALUES(?,?,?,?,?,?,?)", (ch["id"], int(time.time()), 0, 0, 0.0, None, blocked))
        return
    try:
        req = urllib.request.Request(ch["url"], headers={"User-Agent": "Pulse0-Synthetic/1.0"})
        try:
            resp = _urlopen(req, timeout=15); status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        ms = (time.time() - t) * 1000
        ok = int(status == ch["expect_status"] and ms <= ch["max_ms"])
        if not ok:
            err = f"status {status}" if status != ch["expect_status"] else f"slow {ms:.0f}ms > {ch['max_ms']:.0f}ms"
    except Exception as e:
        ms = (time.time() - t) * 1000; err = str(e)[:200]
    u = urlparse(ch["url"])
    if u.scheme == "https":
        try:
            with socket.create_connection((u.hostname, u.port or 443), timeout=8) as s:
                with ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname) as ss:
                    exp = ssl.cert_time_to_seconds(ss.getpeercert()["notAfter"])
                    ssl_days = (exp - time.time()) / 86400
                    if ssl_days < 7 and ok:
                        ok, err = 0, f"certificate expires in {ssl_days:.1f}d"
        except Exception:
            pass
    with db() as c:
        c.execute("INSERT INTO synthetic_runs(check_id,ts,ok,status,ms,ssl_days,err) VALUES(?,?,?,?,?,?,?)", (ch["id"], int(time.time()), ok, status, ms, ssl_days, err))


@app.get("/api/synthetics")
def synthetics():
    rows = q("SELECT * FROM synthetics ORDER BY id")
    for r in rows:
        runs = q("SELECT * FROM synthetic_runs WHERE check_id=? ORDER BY ts DESC LIMIT 40", (r["id"],))
        r["last"] = runs[0] if runs else None
        r["history"] = list(reversed(runs))
        r["uptime"] = (100 * sum(x["ok"] for x in runs) / len(runs)) if runs else None
    return rows


@app.post("/api/synthetics")
async def synth_create(body: SyntheticIn):
    _tier_check("synthetics", "synthetics")
    with db() as c:
        c.execute("INSERT INTO synthetics(name,url,interval_s,expect_status,max_ms) VALUES(?,?,?,?,?)",
                  (body.name, body.url, body.interval_s, body.expect_status, body.max_ms))
    return {"ok": True}


@app.delete("/api/synthetics/{cid}")
def synth_delete(cid: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM synthetics WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "synthetic not found")
        c.execute("DELETE FROM synthetics WHERE id=?", (cid,))
        c.execute("DELETE FROM synthetic_runs WHERE check_id=?", (cid,))
    return {"ok": True}


@app.post("/api/synthetics/{cid}/run")
def synth_run(cid: int):
    ch = q("SELECT * FROM synthetics WHERE id=?", (cid,))
    if not ch:
        raise HTTPException(404)
    run_check(ch[0])
    return {"ok": True}


# ---- log facets (UI sidebar)
@app.get("/api/logs/facets")
def logs_facets(minutes: float = 60, service: str = "", q_: str = ""):
    where, args = ["ts_ns>=?"], [since_ns(minutes)]
    if service:
        where.append("service=?"); args.append(service)
    for tok in q_.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            where.append("(CAST(json_extract(attrs, ?) AS TEXT)=? OR CAST(json_extract(res, ?) AS TEXT)=?)")
            args += [f'$."{k}"', v, f'$."{k}"', v]
        else:
            where.append("body LIKE ?"); args.append(f"%{tok}%")
    w = " AND ".join(where)
    return {
        "service": q(f"SELECT service value, COUNT(*) n FROM logs WHERE {w} GROUP BY service ORDER BY n DESC LIMIT 25", args),
        "severity": q(f"""SELECT CASE WHEN severity>=21 THEN 'FATAL' WHEN severity>=17 THEN 'ERROR' WHEN severity>=13 THEN 'WARN'
                          WHEN severity>=9 THEN 'INFO' WHEN severity>=5 THEN 'DEBUG' WHEN severity>=1 THEN 'TRACE' ELSE 'UNSET' END value, COUNT(*) n
                          FROM logs WHERE {w} GROUP BY value ORDER BY n DESC""", args),
    }


# ---- Pulse0 "intelligence" layer (rule-based, no LLM): insights, home summary, investigate, search
def _pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))] if sorted_vals else 0.0


def _agg(rows):
    """{n, err, err_pct, p95} for (status, dur_ms) tuples."""
    n = len(rows)
    err = sum(1 for st, _ in rows if st == 2)
    return {"n": n, "err": err, "err_pct": 100 * err / n if n else 0.0, "p95": _pct(sorted(d for _, d in rows), .95)}


_SEV_RANK = {"critical": 0, "warning": 1, "info": 2}


@app.get("/api/insights")
@ttl_cached
def insights(minutes: float = 60):
    """Rule-based findings across all signals. Each: {severity, service, title, detail, link, kind}."""
    now = time.time_ns()
    t0 = now - int(minutes * 60e9)
    tp = t0 - int(minutes * 60e9)
    out = []

    def add(sev, kind, svc, title, detail, link):
        out.append({"severity": sev, "kind": kind, "service": svc, "title": title, "detail": detail, "link": link})

    recent_ns = now - int(min(minutes, 5) * 60e9)
    # Grouped scans instead of several queries per service: one pass over both windows' root spans, one for the last
    # minutes, one for error logs (partial index ix_logs_err).
    cur_w, prev_w = {}, {}
    for svc, st, status, dur in qrows("SELECT service, start_ns, status, dur_ms FROM spans "
                                      "WHERE start_ns>=? AND start_ns<? AND (parent_id='' OR kind=2)", (tp, now + 1)):
        (cur_w if st >= t0 else prev_w).setdefault(svc, []).append((status, dur))
    last5s = dict(qrows("SELECT service, COUNT(*) FROM spans WHERE start_ns>=? AND (parent_id='' OR kind=2) GROUP BY service",
                        (recent_ns,)))
    logc = {r[0]: (r[1] or 0, r[2]) for r in qrows("SELECT service, SUM(ts_ns>=?), COUNT(*) FROM logs "
                                                     "WHERE severity>=17 AND ts_ns>=? GROUP BY service", (recent_ns, t0))}
    for s in sorted(set(cur_w) | set(prev_w)):
        cur, prev = _agg(cur_w.get(s, [])), _agg(prev_w.get(s, []))
        if cur["n"] == 0:
            if prev["n"] >= 5:
                add("critical", "silent", s, f"{s} stopped reporting",
                    f"No requests in the last {minutes:g} min (previous window: {prev['n']}).", f"#/services/{s}")
            continue
        last5 = last5s.get(s, 0)
        if last5 == 0 and cur["n"] >= 5:
            add("critical", "silent", s, f"{s} went silent",
                f"{cur['n']} requests earlier in the window but none in the last {min(minutes, 5):g} min.", f"#/services/{s}")
        if cur["n"] >= 5 and cur["err_pct"] >= 5:
            spike = prev["n"] >= 5 and cur["err_pct"] >= 2 * max(prev["err_pct"], 0.5)
            sev = "critical" if cur["err_pct"] >= 20 else "warning"
            title = f"Error rate {'spike' if spike else 'elevated'} on {s}"
            detail = f"{cur['err_pct']:.1f}% of {cur['n']} requests failed" + (f" (was {prev['err_pct']:.1f}%)" if prev["n"] else "")
            add(sev, "error_rate", s, title, detail, f"#/traces?service={s}&errors=1")
        if cur["n"] >= 5 and prev["n"] >= 5 and prev["p95"] > 0 and cur["p95"] > 100 and cur["p95"] > 1.5 * prev["p95"]:
            add("warning", "latency", s, f"p95 latency drift on {s}",
                f"p95 {cur['p95']:.0f}ms vs {prev['p95']:.0f}ms in the prior window (+{100 * (cur['p95'] / prev['p95'] - 1):.0f}%).",
                f"#/services/{s}")
        e5, eall = logc.get(s, (0, 0))
        mins5 = min(minutes, 5)
        if e5 >= 5 and e5 / mins5 > 3 * (eall / max(minutes, 1)):
            add("warning", "log_burst", s, f"Error log burst on {s}",
                f"{e5} error logs in the last {mins5:g} min ({e5 / mins5:.1f}/min vs {eall / max(minutes, 1):.1f}/min average).",
                f"#/logs?service={s}&sev=17")
    for c in q("SELECT * FROM synthetics WHERE enabled=1"):
        last = q("SELECT * FROM synthetic_runs WHERE check_id=? ORDER BY ts DESC LIMIT 1", (c["id"],))
        if last and not last[0]["ok"]:
            add("critical", "synthetic", "", f"Synthetic check '{c['name']}' failing", last[0]["err"] or f"status {last[0]['status']}", "#/synthetics")
    for s in slos_list():
        used = s.get("error_budget_used_pct")
        if used is not None and used >= 75:
            add("critical" if used >= 100 else "warning", "slo", s["service"], f"SLO '{s['name']}' burning budget",
                f"{min(used, 999):.0f}% of error budget used (attainment {s['attainment_pct']}% vs objective {s['objective_pct']}%).", "#/slos")
    for r in q("SELECT * FROM alert_rules WHERE enabled=1 AND state='firing'"):
        add("critical", "alert", r["service"], f"Alert firing: {r['name']}",
            f"{r['signal']} = {(r['last_value'] or 0):.2f} {r['op']} {r['threshold']}", "#/alerts")
    out.sort(key=lambda x: _SEV_RANK[x["severity"]])
    return out


@app.get("/api/home")
@ttl_cached
def home(minutes: float = 60, buckets: int = Query(60, ge=1, le=1000)):
    buckets = max(1, min(int(buckets), 1000))
    t0 = since_ns(minutes)
    w = int(minutes * 60 * 1e9 / buckets)
    rows = q("""SELECT (start_ns-?)/? b, COUNT(*) n, SUM(status=2) err FROM spans
                WHERE start_ns>=? AND (parent_id='' OR kind=2) GROUP BY b""", (t0, w, t0))
    by = {r["b"]: r for r in rows}
    ts = [dict(t=(t0 + i * w) / 1e6, rate=(by[i]["n"] / (w / 1e9)) if i in by else 0,
               err_rate=((by[i]["err"] or 0) / (w / 1e9)) if i in by else 0) for i in range(buckets)]
    svcs = [s for s in services(minutes) if s["n"] > 0]
    n = sum(s["n"] for s in svcs)
    err = sum(s["err"] for s in svcs)
    ins = insights(minutes)
    return {
        "totals": {"requests": n, "errors": err, "err_pct": round(100 * err / n, 2) if n else 0,
                   "services": len(svcs), "rate": n / (minutes * 60),
                   "p95_avg": sum(s["p95"] for s in svcs) / len(svcs) if svcs else 0,
                   "logs": q("SELECT COUNT(*) n FROM logs WHERE ts_ns>=?", (t0,))[0]["n"],
                   "error_logs": q("SELECT COUNT(*) n FROM logs WHERE ts_ns>=? AND severity>=17", (t0,))[0]["n"]},
        "timeseries": ts,
        "top_errors": sorted(svcs, key=lambda s: -s["err_pct"])[:5],
        "top_latency": sorted(svcs, key=lambda s: -s["p95"])[:5],
        "insight_counts": {k: sum(1 for i in ins if i["severity"] == k) for k in ("critical", "warning", "info")},
        "events": alert_events()[:6],
        "slos": slos_list(),
    }


@app.get("/api/search")
def search(q_: str = "", limit: int = 6):
    """Command-palette search: services, trace ids / root names, metric names."""
    s = q_.strip()
    if not s:
        return {"services": [], "traces": [], "metrics": []}
    like = f"%{s}%"
    return {
        "services": q("SELECT DISTINCT service name FROM spans WHERE service LIKE ? LIMIT ?", (like, limit)),
        "traces": q("""SELECT trace_id, name, service, dur_ms FROM spans WHERE parent_id='' AND (trace_id LIKE ? OR name LIKE ?)
                       ORDER BY start_ns DESC LIMIT ?""", (like, like, limit)),
        "metrics": q("SELECT DISTINCT name FROM metrics WHERE name LIKE ? LIMIT ?", (like, limit)),
    }


@app.get("/api/investigate")
def investigate(q_: str = "", minutes: float = 60):
    """Rule-based 'Ask Pulse0': maps a question to an analysis over the stored signals."""
    text = q_.lower().strip()
    svcs = [s for s in services(minutes) if s["n"] > 0]
    findings, suggestions = [], []
    if any(k in text for k in ("slow", "latenc", "p95", "p99", "duration")):
        intent, title = "slow_services", "Slowest services"
        for s in sorted(svcs, key=lambda s: -s["p95"])[:5]:
            findings.append({"label": s["service"], "value": f"p95 {s['p95']:.0f}ms · p99 {s['p99']:.0f}ms",
                             "severity": "warning" if s["p95"] > 500 else "info", "link": f"#/services/{s['service']}"})
        slow = q("""SELECT trace_id, name, service, dur_ms FROM spans WHERE start_ns>=? AND parent_id='' ORDER BY dur_ms DESC LIMIT 3""",
                 (since_ns(minutes),))
        for t in slow:
            findings.append({"label": f"Slow trace {t['name']}", "value": f"{t['dur_ms']:.0f}ms in {t['service']}",
                             "severity": "info", "link": f"#/traces?id={t['trace_id']}"})
        summary = (f"{findings[0]['label']} has the highest p95 latency ({findings[0]['value']})." if findings else "No traffic in this window.")
    elif any(k in text for k in ("saturat", "cpu", "memory", "mem ", "disk", "resource")):
        intent, title = "saturation", "Resource saturation"
        rows = q("""SELECT service, name, MAX(value) mx, AVG(value) av FROM metrics WHERE ts_ns>=?
                    AND (name LIKE '%cpu%' OR name LIKE '%memory%' OR name LIKE '%utiliz%' OR name LIKE '%usage%')
                    GROUP BY service, name ORDER BY mx DESC LIMIT 8""", (since_ns(minutes),))
        for r in rows:
            findings.append({"label": f"{r['service']} · {r['name']}", "value": f"max {r['mx']:.3g} · avg {r['av']:.3g}",
                             "severity": "info", "link": f"#/metrics?name={r['name']}&service={r['service']}"})
        summary = "Highest utilisation-style metrics shown." if rows else "No CPU/memory-style metrics found in this window."
        if not rows:
            suggestions.append("Enable the bundled collector's hostmetrics/kubeletstats receivers to get CPU and memory.")
    elif any(k in text for k in ("log",)):
        intent, title = "error_logs", "Top error logs"
        rows = q("""SELECT service, substr(body,1,120) msg, COUNT(*) n FROM logs WHERE ts_ns>=? AND severity>=17
                    GROUP BY service, msg ORDER BY n DESC LIMIT 6""", (since_ns(minutes),))
        for r in rows:
            findings.append({"label": r["msg"], "value": f"{r['n']}× in {r['service']}", "severity": "warning",
                             "link": f"#/logs?service={r['service']}&sev=17"})
        summary = f"{sum(r['n'] for r in rows)} error logs, grouped by message." if rows else "No error logs in this window."
    elif any(k in text for k in ("error", "broke", "fail", "wrong", "down", "issue", "problem", "5xx", "500")):
        intent, title = "what_broke", "What is failing"
        bad = [s for s in svcs if s["err"] > 0]
        for s in sorted(bad, key=lambda s: -s["err_pct"])[:5]:
            op = q("""SELECT name, COUNT(*) n, SUM(status=2) e FROM spans WHERE service=? AND start_ns>=? AND kind IN (2,5)
                      GROUP BY name HAVING SUM(status=2)>0 ORDER BY e DESC LIMIT 1""", (s["service"], since_ns(minutes)))
            msg = q("""SELECT substr(body,1,140) b FROM logs WHERE service=? AND ts_ns>=? AND severity>=17
                       GROUP BY b ORDER BY COUNT(*) DESC LIMIT 1""", (s["service"], since_ns(minutes)))
            val = f"{s['err_pct']:.1f}% errors ({s['err']}/{s['n']})"
            if op:
                val += f" · worst op {op[0]['name']}"
            if msg:
                val += f" · \"{msg[0]['b']}\""
            findings.append({"label": s["service"], "value": val, "severity": "critical" if s["err_pct"] >= 20 else "warning",
                             "link": f"#/traces?service={s['service']}&errors=1"})
        summary = (f"{len(bad)} service(s) have failing requests; {findings[0]['label']} is the worst." if findings
                   else "No failing requests in this window. All services look healthy.")
    else:
        intent, title = "overview", "Overview"
        ins = insights(minutes)
        for i in ins[:6]:
            findings.append({"label": i["title"], "value": i["detail"], "severity": i["severity"], "link": i["link"]})
        summary = (f"{len(ins)} active insight(s) across {len(svcs)} service(s)." if ins
                   else f"{len(svcs)} service(s) reporting and nothing needs attention.")
        suggestions = ["Which services are slow?", "What is failing right now?", "Show top error logs", "Any resource saturation?"]
    return {"question": q_, "intent": intent, "title": title, "summary": summary, "findings": findings,
            "suggestions": suggestions or ["Which services are slow?", "What is failing right now?", "Show top error logs"]}


ALERT_TEMPLATES = [
    {"id": "high-error-rate", "category": "Availability", "name": "High error rate", "description": "More than 5% of requests fail over 5 minutes.",
     "signal": "error_rate", "expr": "", "op": ">", "threshold": 5, "window_s": 300},
    {"id": "critical-error-rate", "category": "Availability", "name": "Critical error rate", "description": "More than 20% of requests fail over 2 minutes.",
     "signal": "error_rate", "expr": "", "op": ">", "threshold": 20, "window_s": 120},
    {"id": "p95-latency-500", "category": "Latency", "name": "p95 latency above 500ms", "description": "Slow tail latency over 5 minutes.",
     "signal": "p95_latency", "expr": "", "op": ">", "threshold": 500, "window_s": 300},
    {"id": "p95-latency-1s", "category": "Latency", "name": "p95 latency above 1s", "description": "Severely slow requests over 5 minutes.",
     "signal": "p95_latency", "expr": "", "op": ">", "threshold": 1000, "window_s": 300},
    {"id": "traffic-drop", "category": "Availability", "name": "Traffic dropped to zero", "description": "No requests for 5 minutes - service may be down.",
     "signal": "request_rate", "expr": "", "op": "<=", "threshold": 0, "window_s": 300},
    {"id": "traffic-surge", "category": "Traffic", "name": "Traffic surge", "description": "More than 100 requests/second sustained.",
     "signal": "request_rate", "expr": "", "op": ">", "threshold": 100, "window_s": 120},
    {"id": "error-log-burst", "category": "Logs", "name": "Error log burst", "description": "More than 20 error logs in 5 minutes.",
     "signal": "log_errors", "expr": "", "op": ">", "threshold": 20, "window_s": 300},
    {"id": "db-errors", "category": "Logs", "name": "Database errors in logs", "description": "Error logs mentioning 'database' in 5 minutes.",
     "signal": "log_errors", "expr": "database", "op": ">", "threshold": 3, "window_s": 300},
    {"id": "timeout-errors", "category": "Logs", "name": "Timeouts in logs", "description": "Error logs mentioning 'timeout'.",
     "signal": "log_errors", "expr": "timeout", "op": ">", "threshold": 3, "window_s": 300},
    {"id": "synthetic-down", "category": "Synthetics", "name": "Synthetic check failing", "description": "Any failed synthetic run in 5 minutes.",
     "signal": "synthetic_down", "expr": "", "op": ">", "threshold": 0, "window_s": 300},
    {"id": "cpu-high", "category": "Saturation", "name": "CPU utilisation above 85%", "description": "Average CPU utilisation (system.cpu.utilization).",
     "signal": "metric_avg", "expr": "system.cpu.utilization", "op": ">", "threshold": 0.85, "window_s": 300},
    {"id": "mem-high", "category": "Saturation", "name": "Memory utilisation above 90%", "description": "Peak memory utilisation (system.memory.utilization).",
     "signal": "metric_max", "expr": "system.memory.utilization", "op": ">", "threshold": 0.9, "window_s": 300},
    {"id": "k8s-cpu-limit", "category": "Kubernetes", "name": "Container CPU near limit", "description": "k8s.container.cpu_limit_utilization above 90%.",
     "signal": "metric_max", "expr": "k8s.container.cpu_limit_utilization", "op": ">", "threshold": 0.9, "window_s": 300},
    {"id": "k8s-mem-limit", "category": "Kubernetes", "name": "Container memory near limit", "description": "k8s.container.memory_limit_utilization above 90%.",
     "signal": "metric_max", "expr": "k8s.container.memory_limit_utilization", "op": ">", "threshold": 0.9, "window_s": 300},
    {"id": "pod-restarts", "category": "Kubernetes", "name": "Pod restarts", "description": "k8s.container.restarts increased.",
     "signal": "metric_max", "expr": "k8s.container.restarts", "op": ">", "threshold": 3, "window_s": 600},
]


@app.get("/api/alerts/templates")
def alert_templates():
    return ALERT_TEMPLATES


# ---- Claude-backed assistant ("Ask Pulse0")
# Read-only tool use over Pulse0's own query functions. The rule-based /api/investigate
# stays as the fallback: the UI calls it whenever this endpoint reports disabled/error.
AI_MAX_ITERATIONS = 8
AI_MAX_TOOL_CHARS = 12000
AI_MAX_MESSAGES = 20
AI_MAX_MSG_CHARS = 4000

AI_SYSTEM = (
    "You are the observability analyst built into this Pulse0 instance (an OpenTelemetry-native "
    "platform holding traces, logs and metrics). Answer the user's question using ONLY the read-only "
    "tools provided; never guess numbers. Cite concrete evidence: service names, trace ids (full hex "
    "ids), error counts, percentages and latencies. If the data needed is missing or empty, say so "
    "plainly instead of speculating. Be concise: short paragraphs or bullet lists, lead with the "
    "answer, then the evidence, then a suggested next step. Use markdown (bold, lists, `code`)."
)

_MIN = {"type": "number", "description": "Look-back window in minutes (default 60)."}
AI_TOOLS = [
    {"name": "list_services", "description": "Services with request count, error count/%, rate and p50/p95/p99 latency (RED).",
     "input_schema": {"type": "object", "properties": {"minutes": _MIN}}},
    {"name": "service_health", "description": "Health score (0-100), state and reason for one service.",
     "input_schema": {"type": "object", "properties": {"service": {"type": "string"}, "minutes": _MIN}, "required": ["service"]}},
    {"name": "search_traces", "description": "Search recent traces (newest first, max 20). Filter by service, errors only, minimum duration (ms) or free text.",
     "input_schema": {"type": "object", "properties": {"service": {"type": "string"}, "errors": {"type": "boolean"},
                                                       "min_ms": {"type": "number"}, "text": {"type": "string"},
                                                       "minutes": _MIN, "limit": {"type": "integer"}}}},
    {"name": "get_trace", "description": "All spans (and correlated logs) of one trace by its full trace id.",
     "input_schema": {"type": "object", "properties": {"trace_id": {"type": "string"}}, "required": ["trace_id"]}},
    {"name": "search_logs", "description": "Search logs (newest first, max 30). min_severity is OTel severity number (9=INFO, 13=WARN, 17=ERROR).",
     "input_schema": {"type": "object", "properties": {"service": {"type": "string"}, "min_severity": {"type": "integer"},
                                                       "text": {"type": "string"}, "trace_id": {"type": "string"},
                                                       "minutes": _MIN, "limit": {"type": "integer"}}}},
    {"name": "metric_names", "description": "Names of all metrics Pulse0 has seen (with services).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "metric_query", "description": "Time series for one metric. agg is avg|max|min|sum; group_by is an attribute key.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "service": {"type": "string"}, "minutes": _MIN,
                                                       "agg": {"type": "string", "enum": ["avg", "max", "min", "sum"]},
                                                       "group_by": {"type": "string"}}, "required": ["name"]}},
    {"name": "list_insights", "description": "Rule-based Live Insights: current problems (error spikes, latency drift, log bursts, silent services, SLO burn).",
     "input_schema": {"type": "object", "properties": {"minutes": _MIN}}},
    {"name": "list_alerts", "description": "Alert rules with current state, and recent alert events.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_slos", "description": "SLOs with attainment and error-budget status.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _ai_key():
    # Sovereign/data-residency mode: never contact Anthropic, even if a key is configured.
    if SOVEREIGN:
        return ""
    # Hosted mode serves many tenants from one process: never forward their telemetry to Anthropic
    # with the platform's key unless the operator explicitly opts in.
    if HOSTED and os.environ.get("PULSE0_HOSTED_AI") != "1":
        return ""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _ai_model():
    return os.environ.get("PULSE0_AI_MODEL", "").strip() or "claude-sonnet-5-5"


def _ai_client():
    """Build the SDK client lazily so Pulse0 runs fine (rules mode) without the package/key."""
    import anthropic
    return anthropic.Anthropic(api_key=_ai_key(), timeout=60.0, max_retries=1)


def _clip(obj, limit=AI_MAX_TOOL_CHARS):
    """JSON-encode a tool result, shrinking long lists before falling back to a hard cut."""
    def shrink(o, n):
        if isinstance(o, list):
            return [shrink(x, n) for x in o[:n]]
        if isinstance(o, dict):
            return {k: shrink(v, n) for k, v in o.items()}
        if isinstance(o, str) and len(o) > 500:
            return o[:500] + "…"
        return o
    for n in (50, 25, 12, 6):
        out = json.dumps(shrink(obj, n), default=str)
        if len(out) <= limit:
            return out
    return json.dumps(shrink(obj, 3), default=str)[:limit] + "…[truncated]"


def _num(v, default, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _tool_get_trace(a):
    d = trace(str(a.get("trace_id", "")))
    spans = [{"span_id": x["span_id"], "parent_id": x["parent_id"], "service": x["service"], "name": x["name"],
              "dur_ms": round(x["dur_ms"], 2), "error": x["status"] == 2,
              "attrs": dict(list((x["attrs"] or {}).items())[:8])} for x in d["spans"][:40]]
    return {"span_count": len(d["spans"]), "spans": spans, "logs": d["logs"][:15]}


def _tool_dispatch(name, a):
    """Execute one read-only tool. Raises on unknown tool / bad input; the caller reports it to the model."""
    m = _num(a.get("minutes"), 60, 1, 10080)
    if name == "list_services":
        return [r for r in services(m) if r["n"] > 0]
    if name == "service_health":
        return service_health(str(a.get("service", "")), m)
    if name == "search_traces":
        return traces(m, str(a.get("service", "")), bool(a.get("errors", False)), _num(a.get("min_ms"), 0, 0, 1e9),
                      str(a.get("text", "")), int(_num(a.get("limit"), 10, 1, 20)))
    if name == "get_trace":
        return _tool_get_trace(a)
    if name == "search_logs":
        return logs(m, str(a.get("service", "")), int(_num(a.get("min_severity"), 0, 0, 24)), str(a.get("text", "")),
                    str(a.get("trace_id", "")), int(_num(a.get("limit"), 15, 1, 30)))
    if name == "metric_names":
        return metric_names()
    if name == "metric_query":
        agg = a.get("agg") if a.get("agg") in ("avg", "max", "min", "sum") else "avg"
        return metric_query(str(a.get("name", "")), str(a.get("service", "")), m, 30, agg, str(a.get("group_by", "")))
    if name == "list_insights":
        return insights(m)
    if name == "list_alerts":
        return {"rules": alerts(), "events": alert_events()[:20]}
    if name == "list_slos":
        return slos_list()
    raise ValueError(f"unknown tool {name}")


class AssistantMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=AI_MAX_MSG_CHARS)


class AssistantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[AssistantMsg] = Field(min_length=1, max_length=AI_MAX_MESSAGES)
    stream: bool = False

    @field_validator("messages")
    @classmethod
    def _last_is_user(cls, v):
        if v[-1].role != "user":
            raise ValueError("last message must have role 'user'")
        return v


def _safe_err(e):
    """Error text for clients/logs with the API key scrubbed out."""
    msg = f"{type(e).__name__}: {e}"
    k = _ai_key()
    return msg.replace(k, "***") if k else msg


def _assistant_events(messages):
    """Run the Claude tool loop, yielding UI events: tool, tool_result, text, done, error."""
    try:
        cl = _ai_client()
    except Exception as e:  # missing package etc.
        yield {"type": "error", "message": _safe_err(e)}
        return
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
    used = []
    try:
        for it in range(AI_MAX_ITERATIONS):
            resp = cl.messages.create(model=_ai_model(), max_tokens=2048, system=AI_SYSTEM, tools=AI_TOOLS, messages=msgs)
            blocks = list(resp.content)
            text = "".join(b.text for b in blocks if b.type == "text")
            calls = [b for b in blocks if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                for i in range(0, len(text), 48):
                    yield {"type": "text", "text": text[i:i + 48]}
                yield {"type": "done", "model": _ai_model(), "tools": used, "iterations": it + 1}
                return
            msgs.append({"role": "assistant", "content": [
                {"type": "text", "text": b.text} if b.type == "text" else
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input} for b in blocks]})
            results = []
            for c in calls:
                inp = c.input if isinstance(c.input, dict) else {}
                yield {"type": "tool", "name": c.name, "input": inp}
                try:
                    out, ok = _clip(_tool_dispatch(c.name, inp)), True
                except Exception as e:  # report to the model, never crash the loop
                    out, ok = f"error: {e}", False
                used.append(c.name)
                yield {"type": "tool_result", "name": c.name, "ok": ok}
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": out, **({} if ok else {"is_error": True})})
            msgs.append({"role": "user", "content": results})
        yield {"type": "error", "message": f"assistant stopped after {AI_MAX_ITERATIONS} tool rounds without a final answer"}
    except Exception as e:
        yield {"type": "error", "message": _safe_err(e)}


@app.get("/api/assistant/status")
def assistant_status():
    on = bool(_ai_key())
    return {"enabled": on, "model": _ai_model() if on else None, "mode": "claude" if on else "rules"}


@app.post("/api/assistant")
def assistant(body: AssistantIn):
    _tier_gate("AI assistant")
    if not _ai_key():
        return JSONResponse({"error": "assistant_disabled", "mode": "rules", "fallback": "/api/investigate",
                             "message": "ANTHROPIC_API_KEY is not set; use the rule-based /api/investigate."}, status_code=503)
    msgs = [m.model_dump() for m in body.messages]
    if body.stream:
        def sse():
            for ev in _assistant_events(msgs):
                yield f"data: {json.dumps(ev)}\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    text, used, err = "", [], None
    for ev in _assistant_events(msgs):
        if ev["type"] == "text":
            text += ev["text"]
        elif ev["type"] == "tool":
            used.append(ev["name"])
        elif ev["type"] == "error":
            err = ev["message"]
    if err:
        return JSONResponse({"error": "assistant_error", "mode": "rules", "fallback": "/api/investigate", "message": err}, status_code=502)
    return {"mode": "claude", "model": _ai_model(), "answer": text, "tools": used}


# ================================================================ deployment mode + hosted (multi-tenant)
# Everything below is inert unless PULSE0_MODE=hosted, except GET /api/mode and the SSRF helpers.
#
# Model: organization -> projects (each project is its own SQLite file = a hard data silo) -> API keys.
#   ingest key (project)   : POST /v1/*                       (what exporters/collectors use)
#   read key   (project)   : GET /api/*                       (dashboards, read-only API consumers)
#   admin key  (org-wide)  : all /api/* (pick project with x-pulse0-project) + /org/* self-service
# Platform operator: PULSE0_ADMIN_TOKEN guards /admin/* (create orgs, quotas, keys, usage).
# Keys are stored as sha256 hashes; the full key is shown exactly once at creation.
DEFAULT_DAILY_CAP = int(os.environ.get("PULSE0_DEFAULT_DAILY_CAP", "5000000"))  # spans+logs+metric points per org per UTC day
KEY_TYPES = {"ingest": "p0i_", "read": "p0r_", "admin": "p0a_"}
FAIL_LIMIT = int(os.environ.get("PULSE0_AUTH_FAIL_LIMIT", "20"))  # failed auths per IP per minute
FAIL_WINDOW = 60

_PLAT_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS orgs(
  id TEXT PRIMARY KEY, name TEXT UNIQUE, created INTEGER, daily_cap INTEGER, retention_hours REAL);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, org_id TEXT, name TEXT, created INTEGER, UNIQUE(org_id, name));
CREATE TABLE IF NOT EXISTS api_keys(
  id TEXT PRIMARY KEY, org_id TEXT, project_id TEXT, type TEXT, name TEXT, prefix TEXT,
  hash TEXT UNIQUE, created INTEGER, last_used INTEGER, revoked_at INTEGER);
CREATE TABLE IF NOT EXISTS usage(
  project_id TEXT, day TEXT, spans INTEGER DEFAULT 0, logs INTEGER DEFAULT 0, metrics INTEGER DEFAULT 0,
  rejected INTEGER DEFAULT 0, PRIMARY KEY(project_id, day));
"""
_plat_lock = threading.Lock()
_plat = None
if HOSTED:
    os.makedirs(os.path.join(DATA_DIR, "projects"), exist_ok=True)
    _plat = sqlite3.connect(os.path.join(DATA_DIR, "platform.db"), check_same_thread=False)
    _plat.row_factory = sqlite3.Row
    _plat.executescript(_PLAT_SCHEMA)


def pq(sql, args=()):
    with _plat_lock:
        return [dict(r) for r in _plat.execute(sql, args).fetchall()]


def pexec(sql, args=()):
    with _plat_lock:
        cur = _plat.execute(sql, args)
        _plat.commit()
        return cur.rowcount


def _new_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


def _project_path(pid):
    if not re.fullmatch(r"prj_[0-9a-f]{12}", str(pid)):
        raise ValueError("bad project id")
    return os.path.join(DATA_DIR, "projects", pid + ".db")


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _seconds_to_midnight():
    t = time.gmtime()
    return 86400 - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)


def _hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _mint_key(org_id, ktype, name, project_id=None):
    key = KEY_TYPES[ktype] + secrets.token_urlsafe(30)
    kid = _new_id("key")
    pexec("INSERT INTO api_keys(id,org_id,project_id,type,name,prefix,hash,created) VALUES(?,?,?,?,?,?,?,?)",
          (kid, org_id, project_id, ktype, name, key[:10], _hash_key(key), int(time.time())))
    return {"id": kid, "key": key, "prefix": key[:10], "type": ktype, "name": name, "project_id": project_id,
            "note": "Copy this key now. It is stored hashed and cannot be shown again."}


_last_touch = {}


def _verify_key(key):
    """Return the key row (+ its org's quota fields) for a valid, unrevoked key, else None."""
    if not key or len(key) > 200:
        return None
    h = _hash_key(key)
    rows = pq("SELECT k.*, o.daily_cap, o.retention_hours, o.name org_name FROM api_keys k "
              "JOIN orgs o ON o.id=k.org_id WHERE k.hash=? AND k.revoked_at IS NULL", (h,))
    if not rows or not hmac.compare_digest(rows[0]["hash"], h):
        return None
    r, now = rows[0], time.time()
    if now - _last_touch.get(r["id"], 0) > 60:
        _last_touch[r["id"]] = now
        pexec("UPDATE api_keys SET last_used=? WHERE id=?", (int(now), r["id"]))
    return r


def _extract_key(request):
    k = request.headers.get("x-pulse0-key")
    if k:
        return k.strip()
    a = request.headers.get("authorization", "")
    return a[7:].strip() if a[:7].lower() == "bearer " else ""


def _admin_ok(request):
    tok = _extract_key(request)
    return bool(ADMIN_TOKEN) and bool(tok) and hmac.compare_digest(tok.encode(), ADMIN_TOKEN.encode())


_FAILS = collections.defaultdict(collections.deque)


def _client_ip(request):
    if os.environ.get("PULSE0_TRUST_PROXY") == "1":  # only behind a proxy you control
        xf = request.headers.get("x-forwarded-for", "")
        if xf:
            return xf.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _auth_fail(request):
    """401, or 429 once an IP has failed too often. Valid keys are never throttled, so a noisy
    attacker behind a shared proxy IP cannot lock real users out."""
    if len(_FAILS) > 10000:
        _FAILS.clear()
    d, now = _FAILS[_client_ip(request)], time.time()
    while d and now - d[0] > FAIL_WINDOW:
        d.popleft()
    if len(d) >= FAIL_LIMIT:
        return JSONResponse({"error": "too_many_attempts"}, status_code=429, headers={"Retry-After": str(FAIL_WINDOW)})
    d.append(now)
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _org_used_today(org_id):
    return pq("SELECT COALESCE(SUM(u.spans+u.logs+u.metrics),0) n FROM usage u JOIN projects p ON p.id=u.project_id "
              "WHERE p.org_id=? AND u.day=?", (org_id, _today()))[0]["n"]


_USAGE_COLS = {"ingest_traces": "spans", "ingest_logs": "logs", "ingest_metrics": "metrics"}


def _usage_add(ident, fn_name, n):
    col = _USAGE_COLS.get(fn_name)
    if not ident or not ident.get("project_id") or not col or not n:
        return
    pexec(f"INSERT INTO usage(project_id,day,{col}) VALUES(?,?,?) ON CONFLICT(project_id,day) "
          f"DO UPDATE SET {col}={col}+excluded.{col}", (ident["project_id"], _today(), int(n)))


def _resolve_project(ident, request):
    """The project a /api/* request reads: the key's own project, or (org admin key) the one named by
    x-pulse0-project / ?project=, or the org's only project."""
    if ident["project_id"]:
        return ident["project_id"], None
    projs = pq("SELECT id,name FROM projects WHERE org_id=? ORDER BY created", (ident["org_id"],))
    sel = request.headers.get("x-pulse0-project") or request.query_params.get("project") or ""
    if sel:
        for p in projs:
            if sel in (p["id"], p["name"]):
                return p["id"], None
        return None, JSONResponse({"error": "unknown_project", "message": f"no project '{sel}' in this organization"}, status_code=404)
    if len(projs) == 1:
        return projs[0]["id"], None
    return None, JSONResponse({"error": "project_required", "projects": projs,
                               "message": "choose a project with the x-pulse0-project header"}, status_code=400)


_READ_ONLY_POSTS = ("/api/assistant", "/api/investigate")


async def _hosted_auth(request: Request, call_next):
    path, method = request.url.path, request.method
    if method == "OPTIONS":  # CORS preflight carries no credentials
        return await call_next(request)
    if path.startswith("/admin"):
        if not _admin_ok(request):
            return _auth_fail(request)
        return await call_next(request)
    if path in ("/api/mode", "/api/auth/config") or not (path.startswith("/v1/") or path.startswith("/api/") or path.startswith("/org/")):
        return await call_next(request)
    ident = _verify_key(_extract_key(request))
    if not ident:
        return _auth_fail(request)
    kt = ident["type"]
    request.state.ident = ident
    tok = None
    if path.startswith("/v1/"):
        if kt != "ingest":
            return JSONResponse({"error": "forbidden", "message": "an ingest key is required for /v1/*"}, status_code=403)
        cap = ident["daily_cap"]
        if cap is not None and _org_used_today(ident["org_id"]) >= cap:
            pexec("INSERT INTO usage(project_id,day,rejected) VALUES(?,?,1) ON CONFLICT(project_id,day) "
                  "DO UPDATE SET rejected=rejected+1", (ident["project_id"], _today()))
            return JSONResponse({"error": "quota_exceeded", "message": "this organization's daily ingest cap is reached"},
                                status_code=429, headers={"Retry-After": str(_seconds_to_midnight())})
        tok = _cur_db.set(_project_path(ident["project_id"]))
    elif path.startswith("/org/"):
        if kt != "admin":
            return JSONResponse({"error": "forbidden", "message": "an organization admin key is required"}, status_code=403)
    else:
        if kt not in ("read", "admin"):
            return JSONResponse({"error": "forbidden", "message": "a read or admin key is required for /api/*"}, status_code=403)
        if method not in ("GET", "HEAD") and kt != "admin" and path not in _READ_ONLY_POSTS:
            return JSONResponse({"error": "forbidden", "message": "read keys cannot modify data"}, status_code=403)
        pid, err = _resolve_project(ident, request)
        if err:
            return err
        request.state.project_id = pid
        tok = _cur_db.set(_project_path(pid))
    try:
        return await call_next(request)
    finally:
        if tok is not None:
            _cur_db.reset(tok)


# ---- outbound-call guard (webhooks + synthetics). Tenants must not be able to point Pulse0 at the
# cluster network, loopback or the cloud metadata service.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_NOREDIR = urllib.request.build_opener(_NoRedirect)


def _urlopen(req, timeout):
    return _NOREDIR.open(req, timeout=timeout) if HOSTED else urllib.request.urlopen(req, timeout=timeout)


def _blocked_dest(url):
    """Why this URL may not be called from hosted mode, or None. (Self-hosted: always allowed.)"""
    if not HOSTED:
        return None
    u = urlparse(url or "")
    if u.scheme not in ("http", "https") or not u.hostname:
        return "only http(s) URLs are allowed"
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return None  # unresolvable: the request itself will fail
    for i in infos:
        ip = ipaddress.ip_address(i[4][0].split("%")[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return f"destination {ip} is not allowed in hosted mode"
    return None


# ---- usage + overview helpers
def _db_bytes(pid):
    base, total = _project_path(pid), 0
    for sfx in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(base + sfx)
        except OSError:
            pass
    return total


def _org_usage(org_id):
    org = pq("SELECT * FROM orgs WHERE id=?", (org_id,))
    if not org:
        raise HTTPException(404, "organization not found")
    o = org[0]
    days = [time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86400)) for i in range(7)]
    projects = []
    for p in pq("SELECT id,name,created FROM projects WHERE org_id=? ORDER BY created", (org_id,)):
        rows = {r["day"]: r for r in pq("SELECT * FROM usage WHERE project_id=?", (p["id"],))}
        zero = {"spans": 0, "logs": 0, "metrics": 0, "rejected": 0}
        today = {k: (rows.get(_today()) or zero)[k] for k in zero}
        week = {k: sum((rows.get(d) or zero)[k] for d in days) for k in zero}
        projects.append({**p, "today": today, "last7d": week, "db_bytes": _db_bytes(p["id"])})
    return {"day": _today(), "org": {"id": o["id"], "name": o["name"], "daily_cap": o["daily_cap"] or 0,
                                     "used_today": _org_used_today(org_id), "retention_hours": o["retention_hours"]},
            "projects": projects}


def _key_rows(org_id):
    return pq("SELECT id,project_id,type,name,prefix,created,last_used,revoked_at FROM api_keys WHERE org_id=? ORDER BY created", (org_id,))


def _create_project(org_id, name):
    pid = _new_id("prj")
    try:
        pexec("INSERT INTO projects(id,org_id,name,created) VALUES(?,?,?,?)", (pid, org_id, name, int(time.time())))
    except _IntegrityError:
        raise HTTPException(409, f"project '{name}' already exists in this organization")
    _open_db(_project_path(pid)).close()  # create the silo eagerly
    return {"id": pid, "name": name, "org_id": org_id}


def _delete_project(pid):
    pexec("DELETE FROM api_keys WHERE project_id=?", (pid,))
    pexec("DELETE FROM usage WHERE project_id=?", (pid,))
    pexec("DELETE FROM projects WHERE id=?", (pid,))
    path = _project_path(pid)
    with _tenants_guard:
        h = _tenants.pop(path, None)
    if h:  # close under its lock so the file can be removed (Windows keeps open files locked)
        with h[1]:
            h[0].close()
    base = os.path.realpath(os.path.join(DATA_DIR, "projects"))
    for sfx in ("", "-wal", "-shm"):
        target = os.path.realpath(path + sfx)
        if os.path.commonpath([base, target]) != base:
            continue
        try:
            os.remove(target)
        except OSError as e:
            print("could not remove", target, e)


def _need_org(org_id):
    r = pq("SELECT * FROM orgs WHERE id=?", (org_id,))
    if not r:
        raise HTTPException(404, "organization not found")
    return r[0]


def _need_project(org_id, pid):
    r = pq("SELECT * FROM projects WHERE id=? AND org_id=?", (pid, org_id))
    if not r:
        raise HTTPException(404, "project not found")
    return r[0]


def _make_key(org_id, body):
    if body.type in ("ingest", "read"):
        if not body.project_id:
            raise HTTPException(400, f"a {body.type} key must be bound to a project (project_id)")
        _need_project(org_id, body.project_id)
        return _mint_key(org_id, body.type, body.name, body.project_id)
    return _mint_key(org_id, "admin", body.name, None)


def _revoke(key_id, org_id=None, forbid_id=None):
    r = pq("SELECT * FROM api_keys WHERE id=?" + (" AND org_id=?" if org_id else ""), (key_id, org_id) if org_id else (key_id,))
    if not r:
        raise HTTPException(404, "key not found")
    if forbid_id and r[0]["id"] == forbid_id:
        raise HTTPException(400, "you cannot revoke the key you are signed in with")
    pexec("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (int(time.time()), key_id))
    return {"id": key_id, "revoked": True}


# ---- request models
_NAME = r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}$"


class OrgIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=64, pattern=_NAME)
    daily_cap: int = Field(default=DEFAULT_DAILY_CAP, ge=0)  # 0 = unlimited
    retention_hours: float = Field(default=RETENTION_HOURS, ge=1, le=24 * 365)
    with_admin_key: bool = True


class OrgPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=2, max_length=64, pattern=_NAME)
    daily_cap: Optional[int] = Field(default=None, ge=0)
    retention_hours: Optional[float] = Field(default=None, ge=1, le=24 * 365)


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=_NAME)


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ingest", "read", "admin"]
    name: str = Field(min_length=1, max_length=64)
    project_id: Optional[str] = Field(default=None, pattern=r"^prj_[0-9a-f]{12}$")


def _hosted_only():
    if not HOSTED:
        raise HTTPException(404, "Not Found")


from fastapi import APIRouter

admin = APIRouter(prefix="/admin", dependencies=[Depends(_hosted_only)])
org = APIRouter(prefix="/org", dependencies=[Depends(_hosted_only)])


# ---- platform operator API (PULSE0_ADMIN_TOKEN)
@admin.post("/orgs")
def admin_org_create(body: OrgIn):
    oid = _new_id("org")
    try:
        pexec("INSERT INTO orgs(id,name,created,daily_cap,retention_hours) VALUES(?,?,?,?,?)",
              (oid, body.name, int(time.time()), body.daily_cap or None, body.retention_hours))
    except _IntegrityError:
        raise HTTPException(409, f"organization '{body.name}' already exists")
    out = {"id": oid, "name": body.name, "daily_cap": body.daily_cap, "retention_hours": body.retention_hours}
    if body.with_admin_key:
        out["admin_key"] = _mint_key(oid, "admin", "initial admin key")
    return out


@admin.get("/orgs")
def admin_org_list():
    out = []
    for o in pq("SELECT * FROM orgs ORDER BY created"):
        out.append({"id": o["id"], "name": o["name"], "created": o["created"], "daily_cap": o["daily_cap"] or 0,
                    "retention_hours": o["retention_hours"], "used_today": _org_used_today(o["id"]),
                    "projects": pq("SELECT COUNT(*) n FROM projects WHERE org_id=?", (o["id"],))[0]["n"],
                    "active_keys": pq("SELECT COUNT(*) n FROM api_keys WHERE org_id=? AND revoked_at IS NULL", (o["id"],))[0]["n"]})
    return out


@admin.patch("/orgs/{org_id}")
def admin_org_patch(org_id: str, body: OrgPatch):
    _need_org(org_id)
    try:
        if body.name is not None:
            pexec("UPDATE orgs SET name=? WHERE id=?", (body.name, org_id))
        if body.daily_cap is not None:
            pexec("UPDATE orgs SET daily_cap=? WHERE id=?", (body.daily_cap or None, org_id))
        if body.retention_hours is not None:
            pexec("UPDATE orgs SET retention_hours=? WHERE id=?", (body.retention_hours, org_id))
    except _IntegrityError:
        raise HTTPException(409, "organization name already exists")
    return _org_usage(org_id)["org"]


@admin.delete("/orgs/{org_id}")
def admin_org_delete(org_id: str):
    _need_org(org_id)
    for p in pq("SELECT id FROM projects WHERE org_id=?", (org_id,)):
        _delete_project(p["id"])
    pexec("DELETE FROM api_keys WHERE org_id=?", (org_id,))
    pexec("DELETE FROM orgs WHERE id=?", (org_id,))
    return {"deleted": org_id}


@admin.post("/orgs/{org_id}/projects")
def admin_project_create(org_id: str, body: ProjectIn):
    _need_org(org_id)
    return _create_project(org_id, body.name)


@admin.get("/orgs/{org_id}/projects")
def admin_project_list(org_id: str):
    _need_org(org_id)
    return pq("SELECT id,name,created FROM projects WHERE org_id=? ORDER BY created", (org_id,))


@admin.delete("/projects/{project_id}")
def admin_project_delete(project_id: str):
    if not re.fullmatch(r"prj_[0-9a-f]{12}", project_id) or not pq("SELECT 1 FROM projects WHERE id=?", (project_id,)):
        raise HTTPException(404, "project not found")
    _delete_project(project_id)
    return {"deleted": project_id}


@admin.post("/orgs/{org_id}/keys")
def admin_key_create(org_id: str, body: KeyIn):
    _need_org(org_id)
    return _make_key(org_id, body)


@admin.get("/orgs/{org_id}/keys")
def admin_key_list(org_id: str):
    _need_org(org_id)
    return _key_rows(org_id)


@admin.delete("/keys/{key_id}")
def admin_key_revoke(key_id: str):
    return _revoke(key_id)


@admin.get("/usage")
def admin_usage_all():
    return [_org_usage(o["id"]) for o in pq("SELECT id FROM orgs ORDER BY created")]


@admin.get("/orgs/{org_id}/usage")
def admin_usage_org(org_id: str):
    return _org_usage(org_id)


# ---- organization self-service API (org admin key)
@org.get("/me")
def org_me(request: Request):
    ident = request.state.ident
    u = _org_usage(ident["org_id"])
    return {**u, "key": {"id": ident["id"], "type": ident["type"], "prefix": ident["prefix"]},
            "projects": u["projects"], "keys": _key_rows(ident["org_id"])}


@org.get("/projects")
def org_projects(request: Request):
    return _org_usage(request.state.ident["org_id"])["projects"]


@org.post("/projects")
def org_project_create(body: ProjectIn, request: Request):
    return _create_project(request.state.ident["org_id"], body.name)


@org.delete("/projects/{project_id}")
def org_project_delete(project_id: str, request: Request):
    if not re.fullmatch(r"prj_[0-9a-f]{12}", project_id):
        raise HTTPException(404, "project not found")
    _need_project(request.state.ident["org_id"], project_id)
    _delete_project(project_id)
    return {"deleted": project_id}


@org.get("/keys")
def org_keys(request: Request):
    return _key_rows(request.state.ident["org_id"])


@org.post("/keys")
def org_key_create(body: KeyIn, request: Request):
    return _make_key(request.state.ident["org_id"], body)


@org.delete("/keys/{key_id}")
def org_key_revoke(key_id: str, request: Request):
    i = request.state.ident
    return _revoke(key_id, org_id=i["org_id"], forbid_id=i["id"])


@org.get("/usage")
def org_usage(request: Request):
    return _org_usage(request.state.ident["org_id"])


app.include_router(admin)
app.include_router(org)


_disc = {"at": 0.0, "doc": None}


def _idp_request(url, form=None, headers=None):
    """GET (or form-POST) JSON from the identity provider. Tests replace this with a fake IdP."""
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("IdP url must be http(s)")
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _discovery():
    if _disc["doc"] and time.time() - _disc["at"] < 3600:
        return _disc["doc"]
    doc = _idp_request(OIDC["issuer"] + "/.well-known/openid-configuration")
    if not doc.get("authorization_endpoint") or not doc.get("token_endpoint"):
        raise HTTPException(502, "identity provider discovery document is missing endpoints")
    _disc.update(at=time.time(), doc=doc)
    return doc


def _need_sso():
    if not IS_PRO:
        raise HTTPException(403, {"error": "pro_required", "message": "SSO is available on the Pro tier.",
                                  "tier": "community", "upgrade_url": "https://pulse0.dev/pricing"})
    if not sso_enabled():
        raise HTTPException(404, "SSO is not configured")


def _redirect_uri(request):
    return OIDC["redirect_url"] or str(request.base_url).rstrip("/") + "/auth/callback"


def _secure_cookie(request):
    return _redirect_uri(request).startswith("https://") or request.url.scheme == "https"


def _safe_next(n):
    n = n or "/"
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


def _login_page(msg, status):
    import html
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Sign-in failed</title><body style='font:15px system-ui;"
                        f"max-width:520px;margin:15vh auto;padding:0 16px'><h2>Sign-in failed</h2><p>{html.escape(msg)}</p>"
                        f"<p><a href='/auth/login'>Try again</a></p></body>", status_code=status)


def _claim_path(claims, path):
    cur = claims
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _role_claim_value(claims):
    name = OIDC["role_claim"]
    return claims[name] if name in claims else _claim_path(claims, name)  # also supports nested paths like realm_access.roles


def _role_from_claims(claims):
    v = _role_claim_value(claims)
    vals = {x for x in re.split(r"[,\s]+", v) if x} if isinstance(v, str) else {str(x) for x in (v or [])}
    for role, key in (("admin", "admin_groups"), ("editor", "editor_groups"), ("viewer", "viewer_groups")):
        if vals & set(OIDC[key]):
            return role
    return OIDC["default_role"] or None


@app.get("/auth/login")
def auth_login(request: Request, next: str = "/"):
    _need_sso()
    disc = _discovery()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    params = {"response_type": "code", "client_id": OIDC["client_id"], "redirect_uri": _redirect_uri(request),
              "scope": OIDC["scopes"], "state": state, "nonce": nonce,
              "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}
    ep = disc["authorization_endpoint"]
    resp = RedirectResponse(ep + ("&" if "?" in ep else "?") + urllib.parse.urlencode(params), status_code=302)
    resp.set_cookie("p0_oidc", _sign({"s": state, "n": nonce, "v": verifier, "next": _safe_next(next), "exp": time.time() + 600}),
                    max_age=600, httponly=True, samesite="lax", secure=_secure_cookie(request), path="/auth")
    return resp


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    _need_sso()
    if error:
        return _login_page(f"The identity provider returned an error: {error}", 400)
    st = _unsign(request.cookies.get("p0_oidc", ""))
    if not st or not code or not state or not hmac.compare_digest(str(st.get("s", "")), state):
        return _login_page("The sign-in session expired or was tampered with. Please start again.", 400)
    try:
        disc = _discovery()
        tok = _idp_request(disc["token_endpoint"], {
            "grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(request),
            "client_id": OIDC["client_id"], "client_secret": OIDC["client_secret"], "code_verifier": st["v"]})
        claims = json.loads(_unb64(tok["id_token"].split(".")[1]))  # received over TLS straight from the issuer's token endpoint
        if _role_claim_value(claims) is None and disc.get("userinfo_endpoint") and tok.get("access_token"):
            claims = {**_idp_request(disc["userinfo_endpoint"], headers={"Authorization": "Bearer " + tok["access_token"]}), **claims}
    except HTTPException:
        raise
    except Exception as e:
        print("oidc callback error:", e)
        return _login_page("Could not complete sign-in with the identity provider.", 502)
    aud = claims.get("aud")
    aud = aud if isinstance(aud, list) else [aud]
    if str(claims.get("iss", "")).rstrip("/") != OIDC["issuer"]:
        return _login_page("The identity token was issued by an unexpected issuer.", 403)
    if OIDC["client_id"] not in aud:
        return _login_page("The identity token was not issued for this application.", 403)
    if float(claims.get("exp", 0)) < time.time() - 30:
        return _login_page("The identity token has expired.", 403)
    if not hmac.compare_digest(str(claims.get("nonce", "")), st["n"]):
        return _login_page("The identity token does not match this sign-in attempt.", 403)
    email = str(claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "")
    if not email:
        return _login_page("The identity provider did not return an email or user name.", 403)
    if claims.get("email_verified") is False:
        return _login_page("Your email address is not verified with the identity provider.", 403)
    if OIDC["allowed_domains"] and email.rsplit("@", 1)[-1].lower() not in OIDC["allowed_domains"]:
        return _login_page("Your email domain is not allowed to sign in here.", 403)
    role = _role_from_claims(claims)
    if not role:
        _audit(dict(actor=email, role="none", via="session"), "LOGIN", "/auth/callback", 403, "no role mapped")
        return _login_page("Your account has no Pulse0 role. Ask an administrator to add you to a Pulse0 group.", 403)
    ttl = OIDC["session_ttl_h"] * 3600
    resp = RedirectResponse(_safe_next(st.get("next")), status_code=302)
    resp.set_cookie("p0_session", _sign({"sub": str(claims.get("sub", "")), "email": email, "name": str(claims.get("name", "")),
                                         "role": role, "exp": time.time() + ttl}),
                    max_age=int(ttl), httponly=True, samesite="lax", secure=_secure_cookie(request), path="/")
    resp.delete_cookie("p0_oidc", path="/auth")
    _audit(dict(actor=email, role=role, via="session"), "LOGIN", "/auth/callback", 302)
    return resp


@app.post("/auth/logout")
def auth_logout(request: Request):
    origin = request.headers.get("origin")
    if origin and urllib.parse.urlparse(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "cross-origin request refused")
    sess = _unsign(request.cookies.get("p0_session", ""))
    if sess:
        _audit(dict(actor=sess.get("email", "?"), role=sess.get("role", "?"), via="session"), "LOGOUT", "/auth/logout", 200)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("p0_session", path="/")
    return resp


@app.get("/api/auth/config")
def auth_config():
    """Public: what the sign-in screen needs before anyone is authenticated."""
    return {"sso": {"enabled": sso_enabled(), "provider": OIDC["provider_name"], "login_url": "/auth/login"},
            "token_required": bool(AUTH_TOKEN or READ_TOKEN) or HOSTED}


@app.get("/api/me")
def api_me(request: Request):
    who = getattr(request.state, "who", None)
    if who:
        return {k: who.get(k, "") for k in ("actor", "name", "role", "via")}
    return {"actor": "hosted-key", "name": "", "role": None, "via": "hosted"}


@app.get("/api/audit")
def api_audit(limit: int = 200, actor: str = ""):
    """Who changed what (admin role). Mutating API calls plus sign-ins/outs."""
    where, args = ["1=1"], []
    if actor:
        where.append("actor=?"); args.append(actor)
    return q(f"SELECT * FROM audit WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?", (*args, max(1, min(limit, 1000))))


@app.get("/api/mode")
def api_mode():
    """Which deployment mode this server runs in (public: the UI needs it before sign-in)."""
    trial = None
    if _TRIAL_START and not _LICENSE_VALID:
        trial = {"active": _TRIAL_ACTIVE, "days_left": _TRIAL_DAYS_LEFT,
                 "total_days": TRIAL_DAYS, "expired": not _TRIAL_ACTIVE and _TRIAL_DAYS_LEFT == 0}
    return {"mode": MODE, "tier": TIER, "is_pro": IS_PRO,
            "limits": TIER_LIMITS if not IS_PRO else None,
            "trial": trial,
            "features": {"multi_tenant": HOSTED, "organizations": HOSTED, "quotas": HOSTED,
                         "admin_api": HOSTED, "sovereign": SOVEREIGN,
                         "ai_assistant": IS_PRO and bool(_ai_key()),
                         "sso": IS_PRO, "rbac": IS_PRO, "postgres": IS_PRO or USE_PG,
                         "unlimited_dashboards": IS_PRO, "unlimited_alerts": IS_PRO,
                         "unlimited_slos": IS_PRO, "unlimited_services": IS_PRO}}


# ---- background workers
def _tenant_silos():
    """(db path or None, retention hours) for every database the background worker must service."""
    if not HOSTED:
        return [(None, RETENTION_HOURS)]
    return [(_project_path(r["pid"]), r["rh"] or RETENTION_HOURS)
            for r in pq("SELECT p.id pid, o.retention_hours rh FROM projects p JOIN orgs o ON o.id=p.org_id")]


def worker_tick(state, now=None):
    """One maintenance pass (alert rules, synthetics, retention) over every tenant silo.
    A failure in one tenant is logged and does not stop the others."""
    now = now or time.time()
    do_alert = now - state["last_alert"] >= 15
    do_prune = now - state["last_prune"] >= 600
    for path, retention in _tenant_silos():
        tok = _cur_db.set(path)
        try:
            if do_alert:
                evaluate_rules()
            for ch in q("SELECT * FROM synthetics WHERE enabled=1"):
                r = q("SELECT MAX(ts) t FROM synthetic_runs WHERE check_id=?", (ch["id"],))[0]["t"] or 0
                if now - r >= ch["interval_s"]:
                    run_check(ch)
            if do_prune:
                cut = time.time_ns() - int(retention * 3600e9)
                with db() as c:
                    c.execute("DELETE FROM spans WHERE start_ns<?", (cut,)); c.execute("DELETE FROM logs WHERE ts_ns<?", (cut,))
                    c.execute("DELETE FROM metrics WHERE ts_ns<?", (cut,))
                    c.execute("DELETE FROM audit WHERE ts<?", (time.time_ns() - int(AUDIT_DAYS * 86400e9),))
                    c.execute("DELETE FROM synthetic_runs WHERE ts<?", (int(time.time() - retention * 3600),))
        except Exception as e:
            print("worker error:", e)
        finally:
            _cur_db.reset(tok)
    if do_alert:
        state["last_alert"] = now
    if do_prune:
        state["last_prune"] = now


def worker():
    state = {"last_alert": 0, "last_prune": 0}
    while True:
        try:
            worker_tick(state)
        except Exception as e:
            print("worker error:", e)
        time.sleep(5)


def cache_warmer():
    """Self-hosted only: while someone has used the dashboards in the last 5 minutes, keep the heavy aggregates warm
    so page loads are instant. Idle instances do no extra work. Starts warm so the first visit after a restart is fast."""
    _last_use[0] = time.time()
    while True:
        busy = 0.0
        if CACHE_TTL > 0 and time.time() - _last_use[0] < 300:
            t = time.time(); _warming.on = True
            try:
                for m in (15, 60):
                    services.refresh(float(m)); insights.refresh(float(m)); home.refresh(float(m), 60); service_map.refresh(float(m))
            except Exception as e:
                print("cache warmer error:", e)
            finally:
                _warming.on = False
            busy = time.time() - t
        time.sleep(max(8.0, busy))  # never more than ~50% duty cycle


@app.on_event("startup")
def _start():
    threading.Thread(target=worker, daemon=True).start()
    if not HOSTED:
        threading.Thread(target=cache_warmer, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("PULSE0_HOST", "127.0.0.1"), port=int(os.environ.get("PULSE0_PORT", "4318")))
