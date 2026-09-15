#!/usr/bin/env python3
"""The web side: API and static files.

This server only reads. There is no route that runs an arbitrary command,
executes arbitrary SQL or writes files on disk; the only thing the API can
change is the dashboard's own config.json.

The heavy lifting happens in the collector, so API requests are almost always
served from ready snapshots in SQLite and cost single-digit milliseconds.
"""
import json
import os
import re
import sys
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import VERSION, auth, dbs, net, pg, services, storage, store, sysinfo  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
COOKIE = "srvmon_session"

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".ico": "image/x-icon", ".json": "application/json; charset=utf-8",
                 ".woff2": "font/woff2"}


# ---------- helpers ----------

def _now():
    return int(time.time())


def redaction(cfg):
    """Pairs for the demo mode, longest original first.

    Replacing a shorter string first would eat the longer one: with both
    "example.com" and "www.example.com" in the rules, the short one would
    match inside the long one and leave a mangled tail.
    """
    conf = cfg.get("redact") or {}
    if not conf.get("enabled"):
        return []
    # Substitution happens over the serialised answer, so a purely numeric rule
    # would also rewrite unrelated numbers — sizes, percentages, timestamps.
    # Such rules are ignored; mask names and addresses instead.
    pairs = [(str(k), str(v)) for k, v in (conf.get("replace") or {}).items()
             if k and not str(k).isdigit()]
    # A replacement that equals some other original would be turned back by
    # unredact_text and break the page's own links. Such a pair is dropped.
    originals = {k for k, _ in pairs}
    pairs = [(k, v) for k, v in pairs if v not in originals or v == k]
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def redact_text(text, pairs):
    for original, replacement in pairs:
        text = text.replace(original, replacement)
    return text


def unredact_text(text, pairs):
    """Turn a masked identifier from the URL back into the real one.

    While the demo mode is on, the page builds its links out of masked values,
    so a request comes back as /domains/site-a. Without this the page would
    answer 404 to its own link.
    """
    for original, replacement in pairs:
        if replacement:
            text = text.replace(replacement, original)
    return text


def series(sql, params, fields):
    rows = store.rows(sql, params)
    return [{f: r.get(f) for f in fields} for r in rows]


def range_seconds(name, default=86400):
    return {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400,
            "30d": 30 * 86400, "90d": 90 * 86400}.get(name, default)


def bucket_for(span):
    """The longer the range, the coarser the bucket: a chart should be drawn
    from hundreds of points, not from tens of thousands."""
    if span <= 3600:
        return 60
    if span <= 6 * 3600:
        return 300
    if span <= 86400:
        return 900
    if span <= 7 * 86400:
        return 3600
    return 6 * 3600


def domain_by_id(cfg, ident):
    for d in cfg.get("domains", []):
        if d["id"] == ident or d["domain"] == ident:
            return d
    return None


def traffic_sum(domain, since):
    row = store.one(
        "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(c2xx),0) c2xx,"
        " COALESCE(SUM(c3xx),0) c3xx, COALESCE(SUM(c4xx),0) c4xx,"
        " COALESCE(SUM(c5xx),0) c5xx, COALESCE(SUM(bytes),0) bytes"
        " FROM domain_traffic WHERE domain=? AND ts>?", (domain, since))
    return row or {}


def uptime_pct(domain, since):
    row = store.one(
        "SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok FROM domain_checks"
        " WHERE domain=? AND ts>?", (domain, since))
    if not row or not row["n"]:
        return None
    return round(100.0 * row["ok"] / row["n"], 2)


# ---------- API handlers ----------

def api_overview(q, cfg):
    system, sys_ts = store.get_latest("system", {})
    svcs = store.get_latest("services", [])[0] or []
    alerts_now = store.get_latest("alerts", [])[0] or []
    dbs_latest = store.get_latest("databases", {})[0] or {}
    ssl_latest = store.get_latest("ssl", {})[0] or {}
    health_latest = store.get_latest("domain_health", {})[0] or {}
    disk_dirs = store.get_latest("disk_dirs", [])[0] or []
    whois_latest = store.get_latest("whois", {})[0] or {}
    day = _now() - 86400
    hour = _now() - 3600

    doms = []
    for d in store.domains(cfg):
        hl = (health_latest.get(d["id"]) or {}).get("health") or {}
        be = (health_latest.get(d["id"]) or {}).get("backend") or {}
        t24 = traffic_sum(d["domain"], day)
        t1 = traffic_sum(d["domain"], hour)
        doms.append({
            "id": d["id"], "domain": d["domain"], "title": d.get("title"),
            "state": hl.get("state", "unknown"), "resp_ms": hl.get("resp_ms"),
            "https_status": hl.get("https_status"), "error": hl.get("error"),
            "backend_ok": be.get("ok"), "backend_port": d.get("backend_port"),
            "requests_24h": t24.get("requests", 0), "requests_1h": t1.get("requests", 0),
            "rpm": round((t1.get("requests", 0) or 0) / 60.0, 2),
            "errors_24h": (t24.get("c5xx", 0) or 0) + (t24.get("c4xx", 0) or 0),
            "c5xx_24h": t24.get("c5xx", 0), "c4xx_24h": t24.get("c4xx", 0),
            "bytes_24h": t24.get("bytes", 0),
            "ssl_days": (ssl_latest.get(d["id"]) or {}).get("days_left"),
            "ssl_status": (ssl_latest.get(d["id"]) or {}).get("status"),
            "reg_days": (whois_latest.get(d["id"]) or {}).get("days_left"),
            "reg_status": (whois_latest.get(d["id"]) or {}).get("status"),
            "reg_till": (whois_latest.get(d["id"]) or {}).get("paid_till"),
            "uptime_24h": uptime_pct(d["domain"], day),
            "project_dir": d.get("project_dir"),
            "disk": next((x["bytes"] for x in disk_dirs
                          if x["path"] == d.get("project_dir")), None),
        })

    db_list = dbs_latest.get("list", [])
    user_dbs = [d for d in db_list if not d.get("system")]
    def engine_total(engine):
        return sum(d["size"] for d in db_list
                   if d["engine"] == engine and d.get("size") and not d.get("system"))

    total_mysql = engine_total("mysql")
    total_sqlite = engine_total("sqlite")
    total_pg = engine_total("postgres")
    biggest = max(user_dbs, key=lambda d: d.get("size") or 0) if user_dbs else None

    svc_down = [s for s in svcs if s.get("active") not in ("active", "activating")]
    return {
        "ts": _now(), "system_ts": sys_ts, "system": system,
        "domains": doms,
        "summary_domains": {
            "total": len(doms),
            "up": sum(1 for d in doms if d["state"] == "ok"),
            "warning": sum(1 for d in doms if d["state"] == "warning"),
            "down": sum(1 for d in doms if d["state"] == "down"),
            "requests_24h": sum(d["requests_24h"] or 0 for d in doms),
            "c5xx_24h": sum(d["c5xx_24h"] or 0 for d in doms),
            "backends": sum(1 for d in doms if d.get("backend_port")),
        },
        "summary_db": {
            "count": len(user_dbs), "mysql_total": total_mysql,
            "sqlite_total": total_sqlite, "pg_total": total_pg,
            "biggest": {"name": biggest["name"], "size": biggest["size"],
                        "engine": biggest["engine"]} if biggest else None,
            "tables": sum(d.get("tables") or 0 for d in user_dbs),
            "connections": int(dbs_latest.get("mysql_status", {}).get("Threads_connected", 0) or 0),
            "mysql_status": dbs_latest.get("mysql_status", {}),
            "pg_status": dbs_latest.get("pg_status", {}),
        },
        "services": {"total": len(svcs), "down": len(svc_down),
                     "down_list": [s["name"] for s in svc_down]},
        "alerts": alerts_now,
        "disk_dirs": sorted([d for d in disk_dirs if d.get("bytes")],
                            key=lambda d: -(d["bytes"] or 0)),
    }


def api_domains(q, cfg):
    ov = api_overview(q, cfg)
    health_latest = store.get_latest("domain_health", {})[0] or {}
    ssl_latest = store.get_latest("ssl", {})[0] or {}
    whois_latest = store.get_latest("whois", {})[0] or {}
    out = []
    for d in store.domains(cfg, only_enabled=False):
        base = next((x for x in ov["domains"] if x["id"] == d["id"]), None)
        hl = (health_latest.get(d["id"]) or {}).get("health") or {}
        s = ssl_latest.get(d["id"]) or {}
        row = base or {"id": d["id"], "domain": d["domain"], "state": "disabled"}
        row.update({
            "enabled": d.get("enabled", True),
            "service": d.get("service"), "backend_port": d.get("backend_port"),
            "project_dir": d.get("project_dir"), "title": d.get("title"),
            "http_status": hl.get("http_status"),
            "ssl_expires": s.get("not_after"), "ssl_issuer": s.get("issuer"),
            "ssl_days": s.get("days_left"), "ssl_status": s.get("status"),
            "last_check": hl.get("ts"),
            "registration": whois_latest.get(d["id"], {}),
            "databases": [db.get("name") for db in d.get("databases", [])],
        })
        out.append(row)
    return {"domains": out, "ts": _now()}


def api_domain_detail(ident, q, cfg):
    d = domain_by_id(cfg, ident)
    if not d:
        return None
    now = _now()
    span = range_seconds(q.get("range", ["24h"])[0])
    bucket = bucket_for(span)
    since = now - span
    health_latest = (store.get_latest("domain_health", {})[0] or {}).get(d["id"], {})
    ssl_latest = (store.get_latest("ssl", {})[0] or {}).get(d["id"], {})
    whois_latest = (store.get_latest("whois", {})[0] or {}).get(d["id"], {})

    traffic = store.rows(
        "SELECT (ts/?)*? ts, SUM(requests) requests, SUM(c2xx) c2xx, SUM(c3xx) c3xx,"
        " SUM(c4xx) c4xx, SUM(c5xx) c5xx, SUM(bytes) bytes FROM domain_traffic"
        " WHERE domain=? AND ts>? GROUP BY (ts/?) ORDER BY ts",
        (bucket, bucket, d["domain"], since, bucket))
    checks = store.rows(
        "SELECT (ts/?)*? ts, AVG(resp_ms) resp_ms, MAX(resp_ms) max_ms, MIN(ok) ok,"
        " COUNT(*) n, SUM(ok) oks FROM domain_checks WHERE domain=? AND ts>?"
        " GROUP BY (ts/?) ORDER BY ts", (bucket, bucket, d["domain"], since, bucket))
    resp_stats = store.one(
        "SELECT AVG(resp_ms) avg_ms, MAX(resp_ms) max_ms, MIN(resp_ms) min_ms,"
        " COUNT(*) n, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failed"
        " FROM domain_checks WHERE domain=? AND ts>?", (d["domain"], since)) or {}

    svc = services.unit_status(d["service"]) if d.get("service") else None
    proc = None
    if svc and svc.get("pid"):
        procs = store.get_latest("processes", [])[0] or []
        proc = next((p for p in procs if p["pid"] == svc["pid"]), None)
        if not proc:
            all_p = sysinfo.processes(limit=400)
            proc = next((p for p in all_p if p["pid"] == svc["pid"]), None)

    errors = store.rows(
        "SELECT ts, status, line, source FROM recent_errors WHERE domain=?"
        " ORDER BY ts DESC LIMIT 40", (d["domain"],))
    disk_dirs = store.get_latest("disk_dirs", [])[0] or []
    disk = next((x["bytes"] for x in disk_dirs if x["path"] == d.get("project_dir")), None)

    return {
        "domain": d["domain"], "id": d["id"], "title": d.get("title"),
        "config": {k: v for k, v in d.items() if k != "databases"},
        "databases": d.get("databases", []),
        "health": health_latest.get("health", {}),
        "backend_check": health_latest.get("backend"),
        "ssl": ssl_latest,
        "service": svc, "process": proc, "disk": disk,
        "registration": whois_latest,
        "availability": {
            "uptime_24h": uptime_pct(d["domain"], now - 86400),
            "uptime_7d": uptime_pct(d["domain"], now - 7 * 86400),
            "uptime_30d": uptime_pct(d["domain"], now - 30 * 86400),
            "avg_ms": round(resp_stats.get("avg_ms") or 0, 1) if resp_stats.get("avg_ms") else None,
            "max_ms": resp_stats.get("max_ms"), "min_ms": resp_stats.get("min_ms"),
            "checks": resp_stats.get("n"), "failed": resp_stats.get("failed"),
        },
        "traffic": {
            "series": traffic, "bucket": bucket,
            "h1": traffic_sum(d["domain"], now - 3600),
            "h24": traffic_sum(d["domain"], now - 86400),
            "d7": traffic_sum(d["domain"], now - 7 * 86400),
            "d30": traffic_sum(d["domain"], now - 30 * 86400),
        },
        "response_series": checks,
        "errors": errors,
        "note_no_upstream_time": True,
    }


def api_databases(q, cfg):
    latest, ts = store.get_latest("databases", {})
    lst = latest.get("list", []) if latest else []
    now = _now()
    for d in lst:
        key = d.get("name")
        prev = store.one(
            "SELECT size FROM db_snapshots WHERE engine=? AND name=? AND ts<? "
            "ORDER BY ts DESC LIMIT 1", (d["engine"], key, now - 7 * 86400 + 3600))
        if prev and prev["size"]:
            d["growth_7d"] = round(100.0 * ((d.get("size") or 0) - prev["size"]) / prev["size"], 1)
        else:
            d["growth_7d"] = None
        last = store.one(
            "SELECT MAX(ts) ts FROM db_snapshots WHERE engine=? AND name=?",
            (d["engine"], key))
        d["last_snapshot"] = last["ts"] if last else None
    errors = latest.get("error") if latest else None
    if isinstance(errors, str):        # older shape: MySQL error only
        errors = {"mysql": errors}
    return {"databases": lst,
            "mysql_status": latest.get("mysql_status", {}) if latest else {},
            "pg_status": latest.get("pg_status", {}) if latest else {},
            "errors": errors or {}, "ts": ts}


def api_database_detail(ident, q, cfg):
    latest = store.get_latest("databases", {})[0] or {}
    entry = next((d for d in latest.get("list", []) if d.get("id") == ident
                  or d.get("name") == ident), None)
    if not entry:
        return None
    span = range_seconds(q.get("range", ["30d"])[0], 30 * 86400)
    bucket = bucket_for(span)
    hist = store.rows(
        "SELECT (ts/?)*? ts, AVG(size) size, AVG(tables) tables, AVG(conns) conns"
        " FROM db_snapshots WHERE engine=? AND name=? AND ts>? GROUP BY (ts/?) ORDER BY ts",
        (bucket, bucket, entry["engine"], entry["name"], _now() - span, bucket))
    detail = dict(entry)
    if entry["engine"] == "postgres":
        detail.update(pg.database_detail(cfg, entry["name"], 20))
    elif entry["engine"] == "mysql":
        tables, err = dbs.mysql_tables(cfg.get("mysql_defaults_file"), entry["name"], 20)
        detail["tables_list"] = tables
        detail["tables_error"] = err
        pl, _ = dbs.mysql_processlist(cfg.get("mysql_defaults_file"))
        detail["processlist"] = [p for p in pl if p["db"] == entry["name"]]
        detail["mysql_status"] = latest.get("mysql_status", {})
    else:
        info = dbs.sqlite_info(entry.get("path"), entry.get("name"), with_tables=True)
        detail.update(info)
        detail["tables_list"] = info.get("table_list", [])
    detail["history"] = hist
    return detail


def api_storage(q, cfg):
    storage.configure(cfg)
    fs = sysinfo.filesystems()
    dirs = store.get_latest("disk_dirs", [])[0] or []
    now = _now()
    for d in dirs:
        prev = store.one(
            "SELECT bytes FROM disk_usage WHERE path=? AND ts<? ORDER BY ts DESC LIMIT 1",
            (d["path"], now - 7 * 86400))
        d["growth_7d"] = (d["bytes"] - prev["bytes"]) if prev and d.get("bytes") else None
    hist = store.rows(
        "SELECT (ts/3600)*3600 ts, AVG(disk_used) used, AVG(disk_total) total"
        " FROM sys_metrics WHERE ts>? GROUP BY (ts/3600) ORDER BY ts", (now - 7 * 86400,))
    return {"filesystems": fs, "directories": sorted(dirs, key=lambda d: -(d["bytes"] or 0)),
            "history": hist, "allowed_roots": storage.ALLOWED_ROOTS}


def api_browse(q, cfg):
    storage.configure(cfg)
    default_root = storage.ALLOWED_ROOTS[0] if storage.ALLOWED_ROOTS else "/"
    path = (q.get("path") or [default_root])[0]
    return storage.listdir(path)


def api_services(q, cfg):
    svcs = store.get_latest("services", [])[0] or []
    ports = store.get_latest("ports", [])[0] or []
    by_pid = {}
    for p in ports:
        if p.get("pid"):
            by_pid.setdefault(p["pid"], []).append(p["port"])
    for s in svcs:
        s["ports"] = sorted(set(by_pid.get(s.get("pid"), [])))
    return {"services": svcs, "ts": _now()}


def api_service_log(ident, q, cfg):
    known = {s["name"] for s in (store.get_latest("services", [])[0] or [])}
    if ident not in known:
        return None
    return {"name": ident, "lines": services.unit_journal(ident, 40)}


def api_processes(q, cfg):
    procs = store.get_latest("processes", [])[0] or []
    if not procs:
        sysinfo.processes(limit=1)
        time.sleep(0.6)
        procs = sysinfo.processes(limit=60)
        names = sysinfo.user_names()
        for p in procs:
            p["user"] = names.get(p["uid"], str(p["uid"]))
    return {"processes": procs, "ts": _now(), "cores": os.cpu_count(),
            "memory_total": sysinfo.meminfo().get("total")}


def api_network(q, cfg):
    ports = store.get_latest("ports", [])[0] or []
    if not ports:
        ports = net.annotate(net.listening(), cfg)
    net_now = (store.get_latest("system", {})[0] or {}).get("network", {})
    hist = store.rows(
        "SELECT ts, net_rx, net_tx FROM sys_metrics WHERE ts>? ORDER BY ts",
        (_now() - 3600,))
    return {"ports": ports, "network": net_now, "history": hist}


def api_ssl(q, cfg):
    latest = store.get_latest("ssl", {})[0] or {}
    out = []
    for d in store.domains(cfg):
        info = dict(latest.get(d["id"], {}))
        info["domain"] = d["domain"]
        info["id"] = d["id"]
        info["cert_path"] = d.get("ssl_cert")
        out.append(info)
    for key, info in latest.items():
        if key.startswith("extra:"):
            entry = dict(info)
            entry["id"] = None
            out.append(entry)
    return {"certificates": out, "ts": _now()}


def api_alerts(q, cfg):
    active = store.get_latest("alerts", [])[0] or []
    events = store.rows(
        "SELECT id, ts, kind, severity, subject, message, resolved_ts, tpl, params"
        " FROM events ORDER BY ts DESC LIMIT 120")
    for e in events:
        try:
            e["params"] = json.loads(e["params"]) if e.get("params") else {}
        except (ValueError, TypeError):
            e["params"] = {}
    errors = store.rows(
        "SELECT ts, source, domain, status, line FROM recent_errors"
        " ORDER BY ts DESC LIMIT 60")
    return {"active": active, "events": events, "recent_errors": errors}


def api_metrics(q, cfg):
    span = range_seconds(q.get("range", ["24h"])[0])
    now = _now()
    bucket = bucket_for(span)
    if span > 7 * 86400:
        rows = store.rows(
            "SELECT ts, cpu, ram_pct, disk_pct, load1, net_rx, net_tx FROM sys_hourly"
            " WHERE ts>? ORDER BY ts", (now - span,))
    else:
        rows = store.rows(
            "SELECT (ts/?)*? ts, AVG(cpu) cpu,"
            " AVG(100.0*ram_used/NULLIF(ram_total,0)) ram_pct,"
            " AVG(100.0*disk_used/NULLIF(disk_total,0)) disk_pct,"
            " AVG(load1) load1, AVG(net_rx) net_rx, AVG(net_tx) net_tx,"
            " AVG(swap_used) swap_used"
            " FROM sys_metrics WHERE ts>? GROUP BY (ts/?) ORDER BY ts",
            (bucket, bucket, now - span, bucket))
    dom_rows = {}
    for d in store.domains(cfg):
        dom_rows[d["domain"]] = store.rows(
            "SELECT (ts/?)*? ts, SUM(requests) requests, SUM(c5xx) c5xx, SUM(c4xx) c4xx"
            " FROM domain_traffic WHERE domain=? AND ts>? GROUP BY (ts/?) ORDER BY ts",
            (bucket, bucket, d["domain"], now - span, bucket))
    return {"system": rows, "domains": dom_rows, "bucket": bucket, "range": span}


def api_settings(q, cfg):
    return {
        "thresholds": cfg.get("thresholds", {}),
        "intervals": cfg.get("intervals", {}),
        "retention": cfg.get("retention", {}),
        "domains": [{k: v for k, v in d.items()} for d in cfg.get("domains", [])],
        "extra_services": cfg.get("extra_services", []),
        "storage_watch": cfg.get("storage_watch", []),
        "config_path": store.CONFIG_PATH,
        "db_path": store.DB_PATH,
        "version": VERSION,
    }


def settings_save(body, cfg):
    """Only the parts of the config the dashboard is allowed to change.

    Paths to certificates, logs and units are not taken from the browser as-is:
    a domain accepts a fixed set of fields, each validated separately.
    """
    allowed_threshold = set(cfg.get("thresholds", {}).keys())
    changed = []
    if "thresholds" in body:
        for k, v in body["thresholds"].items():
            if k in allowed_threshold:
                try:
                    cfg["thresholds"][k] = float(v) if "." in str(v) else int(v)
                    changed.append("threshold:" + k)
                except (TypeError, ValueError):
                    continue
    if "intervals" in body:
        for k, v in body["intervals"].items():
            if k in cfg.get("intervals", {}):
                try:
                    cfg["intervals"][k] = max(10, int(v))
                    changed.append("interval:" + k)
                except (TypeError, ValueError):
                    continue
    if "domain" in body:
        d = body["domain"]
        ident = str(d.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", ident):
            return {"error": "Identifier: latin letters, digits, hyphen",
                    "code": "bad_id"}, 400
        domain_name = str(d.get("domain", "")).strip().lower()
        if not re.fullmatch(r"[a-z0-9.-]{3,253}", domain_name):
            return {"error": "Invalid domain name", "code": "bad_domain"}, 400
        entry = domain_by_id(cfg, ident) or {}
        new = dict(entry)
        new.update({
            "id": ident, "domain": domain_name,
            "title": str(d.get("title") or domain_name)[:80],
            "enabled": bool(d.get("enabled", True)),
            "project_dir": _clean_path(d.get("project_dir")),
            "access_log": _clean_path(d.get("access_log")),
            "error_log": _clean_path(d.get("error_log")),
            "ssl_cert": _clean_path(d.get("ssl_cert")),
            "service": _clean_unit(d.get("service")),
            "backend_port": _clean_port(d.get("backend_port")),
            "health_path": str(d.get("health_path") or "/")[:200],
        })
        if isinstance(d.get("databases"), list):
            clean_dbs = []
            for db in d["databases"][:20]:
                eng = db.get("engine")
                if eng == "mysql" and re.fullmatch(r"[A-Za-z0-9_]{1,64}", str(db.get("name", ""))):
                    clean_dbs.append({"engine": "mysql", "name": db["name"]})
                elif eng == "sqlite" and _clean_path(db.get("path")):
                    clean_dbs.append({"engine": "sqlite", "path": _clean_path(db["path"]),
                                      "name": str(db.get("name") or "sqlite")[:60]})
            new["databases"] = clean_dbs
        others = [x for x in cfg.get("domains", []) if x["id"] != ident]
        cfg["domains"] = others + [new]
        changed.append("domain:" + ident)
    if body.get("delete_domain"):
        ident = str(body["delete_domain"])
        cfg["domains"] = [x for x in cfg.get("domains", []) if x["id"] != ident]
        changed.append("removed:" + ident)
    store.save_config(cfg)
    return {"ok": True, "changed": changed}, 200


def _clean_path(value):
    if not value:
        return None
    v = str(value)
    if not v.startswith("/") or ".." in v or "\0" in v or len(v) > 300:
        return None
    return v


def _clean_unit(value):
    if not value:
        return None
    v = str(value)
    return v if re.fullmatch(r"[A-Za-z0-9@._-]{1,80}", v) else None


def _clean_port(value):
    try:
        p = int(value)
    except (TypeError, ValueError):
        return None
    return p if 1 <= p <= 65535 else None


ROUTES = {
    "overview": api_overview, "domains": api_domains, "databases": api_databases,
    "storage": api_storage, "services": api_services, "processes": api_processes,
    "network": api_network, "ssl": api_ssl, "alerts": api_alerts,
    "metrics": api_metrics, "settings": api_settings, "browse": api_browse,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "srvmon"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # no log of our own: the dashboard already lives behind nginx

    # ---- responses ----
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200, extra=None):
        body = json.dumps(data, ensure_ascii=False, default=str)
        # Demo mode masks values in the answer itself, so nothing has to be
        # remembered at every call site — and nothing leaks through a field
        # somebody forgot about.
        pairs = getattr(self, "_redact_pairs", None)
        if pairs:
            body = redact_text(body, pairs)
        self._send(code, body.encode("utf-8"), extra=extra)

    def _cookie_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = SimpleCookie(raw)
        except Exception:
            return None
        return c[COOKIE].value if COOKIE in c else None

    def _user(self):
        return auth.session_user(self._cookie_token())

    def _body(self, limit=64 * 1024):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > limit:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ---- routing ----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        q = urllib.parse.parse_qs(parsed.query)

        if path == "/api/ping":
            return self._json({"ok": True, "ts": _now(), "version": VERSION})
        if path == "/api/session":
            user = self._user()
            return self._json({"authenticated": bool(user), "user": user,
                               "has_users": bool(auth.load_users())})

        if path.startswith("/api/"):
            if not self._user():
                return self._json({"error": "authentication required", "code": "auth_required"}, 401)
            return self._api(path[5:], q)

        return self._static(path)

    def do_HEAD(self):
        # HEAD is answered like GET: the body is dropped in _send.
        self.do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/login":
            body = self._body()
            login = str(body.get("login", ""))[:64]
            password = str(body.get("password", ""))[:256]
            time.sleep(0.25)  # slow down guessing
            if auth.authenticate(login, password):
                token, ttl = auth.create_session(login)
                cookie = ("%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
                          % (COOKIE, token, ttl))
                if self.headers.get("X-Forwarded-Proto") == "https":
                    cookie += "; Secure"
                return self._json({"ok": True, "user": login}, extra={"Set-Cookie": cookie})
            return self._json({"error": "Invalid login or password", "code": "bad_credentials"}, 401)
        if path == "/api/logout":
            auth.drop_session(self._cookie_token())
            return self._json({"ok": True}, extra={
                "Set-Cookie": "%s=; Path=/; HttpOnly; Max-Age=0" % COOKIE})
        if path == "/api/settings":
            if not self._user():
                return self._json({"error": "authentication required", "code": "auth_required"}, 401)
            cfg = store.load_config()
            data, code = settings_save(self._body(), cfg)
            return self._json(data, code)
        return self._json({"error": "unknown endpoint", "code": "not_found"}, 404)

    def _api(self, name, q):
        cfg = store.load_config()
        pairs = redaction(cfg)
        self._redact_pairs = pairs
        if pairs:
            name = unredact_text(name, pairs)
        parts = name.split("/")
        head = parts[0]
        try:
            if head == "domains" and len(parts) > 1:
                data = api_domain_detail(urllib.parse.unquote(parts[1]), q, cfg)
                return self._json(data or {"error": "domain not found", "code": "no_domain"}, 200 if data else 404)
            if head == "databases" and len(parts) > 1:
                # A SQLite database id is a file path and contains slashes,
                # so the tail of the route is put back together whole
                data = api_database_detail(urllib.parse.unquote("/".join(parts[1:])), q, cfg)
                return self._json(data or {"error": "database not found", "code": "no_database"}, 200 if data else 404)
            if head == "services" and len(parts) > 2 and parts[2] == "log":
                data = api_service_log(urllib.parse.unquote(parts[1]), q, cfg)
                return self._json(data or {"error": "service not found", "code": "no_service"}, 200 if data else 404)
            fn = ROUTES.get(head)
            if not fn:
                return self._json({"error": "unknown endpoint", "code": "not_found"}, 404)
            return self._json(fn(q, cfg))
        except Exception as exc:
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _static(self, path):
        if path == "/" or path.startswith("/domains") or path.startswith("/databases") \
                or path in ("/storage", "/services", "/processes", "/network", "/ssl",
                            "/alerts", "/settings", "/login"):
            target = os.path.join(WEB, "index.html")
        else:
            safe = os.path.normpath(path).lstrip("/")
            target = os.path.join(WEB, safe)
            if not os.path.realpath(target).startswith(os.path.realpath(WEB)):
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(target):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(target)[1]
        with open(target, "rb") as fh:
            data = fh.read()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        cache = "no-store" if ext == ".html" else "public, max-age=300"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # The default backlog is only 5: a browser opens several connections at
    # once on refresh, and some of them would be refused.
    request_queue_size = 64


def main():
    store.init()
    cfg = store.load_config()
    host = cfg.get("bind_host", "127.0.0.1")
    port = int(cfg.get("bind_port", 8452))
    if not auth.load_users():
        print("WARNING: no users yet. Create one: python3 tools/passwd.py <login>",
              flush=True)
    srv = Server((host, port), Handler)
    print("dashboard listening on http://%s:%d" % (host, port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
