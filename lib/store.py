"""Хранилище метрик. SQLite, потому что панель обязана быть легче того,
за чем она следит: на сервере 3.8 ГБ памяти и она почти вся занята."""
import json
import os
import sqlite3
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "var", "metrics.db")
CONFIG_PATH = os.path.join(BASE, "config.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sys_metrics (
  ts INTEGER PRIMARY KEY, cpu REAL, ram_used INTEGER, ram_total INTEGER,
  swap_used INTEGER, swap_total INTEGER, load1 REAL, load5 REAL, load15 REAL,
  disk_used INTEGER, disk_total INTEGER, net_rx REAL, net_tx REAL,
  temp REAL, procs INTEGER, uptime INTEGER);

CREATE TABLE IF NOT EXISTS sys_hourly (
  ts INTEGER PRIMARY KEY, cpu REAL, ram_pct REAL, disk_pct REAL,
  load1 REAL, net_rx REAL, net_tx REAL, samples INTEGER);

CREATE TABLE IF NOT EXISTS domain_checks (
  ts INTEGER, domain TEXT, ok INTEGER, http_status INTEGER, https_status INTEGER,
  resp_ms REAL, dns_ms REAL, tcp_ms REAL, error TEXT);
CREATE INDEX IF NOT EXISTS ix_dc ON domain_checks(domain, ts);

CREATE TABLE IF NOT EXISTS domain_traffic (
  ts INTEGER, domain TEXT, requests INTEGER, c2xx INTEGER, c3xx INTEGER,
  c4xx INTEGER, c5xx INTEGER, bytes INTEGER, span INTEGER);
CREATE INDEX IF NOT EXISTS ix_dt ON domain_traffic(domain, ts);

CREATE TABLE IF NOT EXISTS db_snapshots (
  ts INTEGER, engine TEXT, name TEXT, size INTEGER, tables INTEGER, conns INTEGER);
CREATE INDEX IF NOT EXISTS ix_db ON db_snapshots(engine, name, ts);

CREATE TABLE IF NOT EXISTS service_status (
  ts INTEGER, name TEXT, active TEXT, sub TEXT, enabled TEXT, pid INTEGER,
  cpu REAL, rss INTEGER, since INTEGER, restarts INTEGER);
CREATE INDEX IF NOT EXISTS ix_svc ON service_status(name, ts);

CREATE TABLE IF NOT EXISTS disk_usage (ts INTEGER, path TEXT, bytes INTEGER);
CREATE INDEX IF NOT EXISTS ix_du ON disk_usage(path, ts);

CREATE TABLE IF NOT EXISTS ssl_certs (
  ts INTEGER, domain TEXT, issuer TEXT, subject TEXT, not_before INTEGER,
  not_after INTEGER, days_left INTEGER, status TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_ssl ON ssl_certs(domain, ts);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, severity TEXT,
  subject TEXT, message TEXT, resolved_ts INTEGER, tpl TEXT, params TEXT);
CREATE INDEX IF NOT EXISTS ix_ev ON events(ts);
CREATE INDEX IF NOT EXISTS ix_ev_open ON events(kind, subject, resolved_ts);

CREATE TABLE IF NOT EXISTS whois_info (
  ts INTEGER, domain TEXT, registrar TEXT, created INTEGER, paid_till INTEGER,
  free_date INTEGER, state TEXT, days_left INTEGER, status TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS ix_whois ON whois_info(domain, ts);

CREATE TABLE IF NOT EXISTS latest (key TEXT PRIMARY KEY, ts INTEGER, payload TEXT);

CREATE TABLE IF NOT EXISTS logpos (path TEXT PRIMARY KEY, inode INTEGER, offset INTEGER);

CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user TEXT, created INTEGER, expires INTEGER);

CREATE TABLE IF NOT EXISTS recent_errors (
  ts INTEGER, source TEXT, domain TEXT, status INTEGER, line TEXT);
CREATE INDEX IF NOT EXISTS ix_re ON recent_errors(ts);
"""

_local = threading.local()


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=20000")
        _local.c = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    migrate()


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg):
    """Пишем через временный файл: оборванная запись оставила бы панель без конфига."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def domains(cfg=None, only_enabled=True):
    cfg = cfg or load_config()
    out = cfg.get("domains", [])
    return [d for d in out if d.get("enabled", True)] if only_enabled else out


def put_latest(key, payload):
    c = conn()
    c.execute("INSERT OR REPLACE INTO latest(key, ts, payload) VALUES(?,?,?)",
              (key, int(time.time()), json.dumps(payload, ensure_ascii=False)))
    c.commit()


def get_latest(key, default=None):
    row = conn().execute("SELECT ts, payload FROM latest WHERE key=?", (key,)).fetchone()
    if not row:
        return default, 0
    return json.loads(row["payload"]), row["ts"]


def migrate():
    """Добавляет колонки, появившиеся после первой версии схемы.
    Нужно тем, кто обновляет панель, а не ставит с нуля."""
    c = conn()
    have = {r["name"] for r in c.execute("PRAGMA table_info(events)")}
    for col in ("tpl", "params"):
        if col not in have:
            c.execute("ALTER TABLE events ADD COLUMN %s TEXT" % col)
    c.commit()


def open_event(kind, subject, severity, message, tpl=None, params=None):
    """Событие открывается один раз: пока проблема не закрыта, дубликатов нет."""
    c = conn()
    row = c.execute(
        "SELECT id FROM events WHERE kind=? AND subject=? AND resolved_ts IS NULL",
        (kind, subject)).fetchone()
    if row:
        return row["id"]
    cur = c.execute(
        "INSERT INTO events(ts, kind, severity, subject, message, tpl, params)"
        " VALUES(?,?,?,?,?,?,?)",
        (int(time.time()), kind, severity, subject, message, tpl,
         json.dumps(params or {}, ensure_ascii=False)))
    c.commit()
    return cur.lastrowid


def close_event(kind, subject, message=None):
    c = conn()
    row = c.execute(
        "SELECT id, message FROM events WHERE kind=? AND subject=? AND resolved_ts IS NULL",
        (kind, subject)).fetchone()
    if not row:
        return None
    c.execute("UPDATE events SET resolved_ts=? WHERE id=?", (int(time.time()), row["id"]))
    c.execute("INSERT INTO events(ts, kind, severity, subject, message, resolved_ts,"
              " tpl, params) VALUES(?,?,?,?,?,?,?,?)",
              (int(time.time()), kind + ".recovered", "info", subject,
               message or "восстановлено", int(time.time()), "recovered",
               json.dumps({"subject": subject}, ensure_ascii=False)))
    c.commit()
    return row["id"]


def open_events():
    return [dict(r) for r in conn().execute(
        "SELECT * FROM events WHERE resolved_ts IS NULL ORDER BY ts DESC").fetchall()]


def rows(sql, params=()):
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql, params=()):
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def write(sql, params=()):
    c = conn()
    c.execute(sql, params)
    c.commit()


def writemany(sql, seq):
    c = conn()
    c.executemany(sql, seq)
    c.commit()
