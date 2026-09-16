#!/usr/bin/env python3
"""The metrics collector.

A separate process: if the web side crashes or gets restarted, history keeps
accumulating. Tasks are spread across intervals — cheap ones run often, costly
ones rarely. Directory scans and parsing of large logs are deliberately put on
a long cycle: the dashboard must not become the load it is meant to watch.
"""
import json
import os
import sys
import signal
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import alerts, dbs, health, net, nginxlog, services, ssl_check, storage, store
from lib import VERSION, pg, whois
from lib import sysinfo

RUN = True


def _stop(signum, frame):
    global RUN
    RUN = False


class Task:
    def __init__(self, name, fn, interval, jitter=0):
        self.name = name
        self.fn = fn
        self.interval = interval
        self.next_run = time.time() + jitter

    def due(self, now):
        return now >= self.next_run

    def run(self, ctx):
        t0 = time.time()
        try:
            self.fn(ctx)
        except Exception as exc:  # one failed task must not stop collection
            log("task %s failed: %s: %s" % (self.name, type(exc).__name__, exc))
        finally:
            self.next_run = time.time() + self.interval
        took = time.time() - t0
        if took > 5:
            log("task %s took %.1f s" % (self.name, took))


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------- tasks ----------

def task_system(ctx):
    cpu = sysinfo.cpu_percent()
    mem = sysinfo.meminfo()
    load = sysinfo.loadavg()
    nets = sysinfo.network()
    fs = sysinfo.filesystems()
    root = sysinfo.root_fs()
    temp = sysinfo.temperature()
    up = sysinfo.uptime()
    ts = int(time.time())
    if cpu is not None:
        store.write(
            "INSERT OR REPLACE INTO sys_metrics(ts,cpu,ram_used,ram_total,swap_used,"
            "swap_total,load1,load5,load15,disk_used,disk_total,net_rx,net_tx,temp,procs,uptime)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, cpu, mem["used"], mem["total"], mem["swap_used"], mem["swap_total"],
             load["load1"], load["load5"], load["load15"], root["used"], root["total"],
             nets["rx_rate"], nets["tx_rate"], temp, load.get("procs"), up))
    snapshot = {
        "cpu": cpu, "memory": mem, "load": load, "network": nets,
        "filesystems": fs, "disk": root, "temp": temp, "uptime": up,
        "os": sysinfo.os_release(), "ts": ts,
    }
    store.put_latest("system", snapshot)
    ctx["system"] = snapshot


def task_processes(ctx):
    procs = sysinfo.processes(limit=60)
    names = sysinfo.user_names()
    for p in procs:
        p["user"] = names.get(p["uid"], str(p["uid"]))
    store.put_latest("processes", procs)
    ctx["processes"] = procs


def task_domain_health(ctx):
    cfg = ctx["cfg"]
    th = cfg.get("thresholds", {})
    out = {}
    for dom in store.domains(cfg):
        hl = health.check(dom, th)
        be = health.check_backend(dom.get("backend_port"), dom.get("health_path") or "/")
        store.write(
            "INSERT INTO domain_checks(ts,domain,ok,http_status,https_status,resp_ms,"
            "dns_ms,tcp_ms,error) VALUES(?,?,?,?,?,?,?,?,?)",
            (hl["ts"], dom["domain"], 1 if hl["ok"] else 0, hl.get("http_status"),
             hl.get("https_status"), hl.get("resp_ms"),
             (hl.get("dns") or {}).get("ms"), (hl.get("tcp") or {}).get("ms"),
             hl.get("error")))
        out[dom["id"]] = {"health": hl, "backend": be}
    store.put_latest("domain_health", out)
    ctx["domain_health"] = out


def task_nginx_logs(ctx):
    cfg = ctx["cfg"]
    now = int(time.time())
    result = {}
    err_rows = []
    for dom in store.domains(cfg):
        path = dom.get("access_log")
        if not path:
            continue
        tailer = ctx["tailers"].setdefault(path, nginxlog.Tailer(path))
        lines = tailer.read_new()
        agg, errors = nginxlog.aggregate(lines)
        span = cfg["intervals"].get("nginx_logs", 60)
        if agg["requests"]:
            store.write(
                "INSERT INTO domain_traffic(ts,domain,requests,c2xx,c3xx,c4xx,c5xx,bytes,span)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (now, dom["domain"], agg["requests"], agg["c2xx"], agg["c3xx"],
                 agg["c4xx"], agg["c5xx"], agg["bytes"], span))
        for e in errors[-40:]:
            if e["status"] >= 500:
                err_rows.append((e["ts"], "nginx", dom["domain"], e["status"], e["line"]))
        result[dom["id"]] = agg
    if err_rows:
        store.writemany(
            "INSERT INTO recent_errors(ts,source,domain,status,line) VALUES(?,?,?,?,?)",
            err_rows)
    store.put_latest("nginx_traffic", result)
    ctx["traffic"] = result
    _collect_error_logs(cfg)


def _collect_error_logs(cfg):
    """Tail of each site's error log: only the appended part is read."""
    rows = []
    for dom in store.domains(cfg):
        path = dom.get("error_log")
        if not path or not os.path.exists(path):
            continue
        key = "err:" + path
        tailer = nginxlog.Tailer(path)
        for line in tailer.read_new(max_bytes=1 << 20)[-30:]:
            if not line.strip():
                continue
            lowered = line.lower()
            if "[error]" in lowered or "[crit]" in lowered or "[alert]" in lowered:
                rows.append((int(time.time()), "nginx-error", dom["domain"], 0, line[:400]))
    if rows:
        store.writemany(
            "INSERT INTO recent_errors(ts,source,domain,status,line) VALUES(?,?,?,?,?)", rows)


def task_services(ctx):
    cfg = ctx["cfg"]
    svcs = services.collect(cfg)
    ts = int(time.time())
    store.writemany(
        "INSERT INTO service_status(ts,name,active,sub,enabled,pid,cpu,rss,since,restarts)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(ts, s["name"], s.get("active"), s.get("sub"), s.get("enabled"), s.get("pid"),
          None, s.get("rss"), s.get("started"), s.get("restarts")) for s in svcs])
    store.put_latest("services", svcs)
    ctx["services"] = svcs
    ports = net.annotate(net.listening(), cfg)
    store.put_latest("ports", ports)
    ctx["ports"] = ports


def task_databases(ctx):
    cfg = ctx["cfg"]
    all_dbs, err = dbs.collect_all(cfg)
    ts = int(time.time())
    store.writemany(
        "INSERT INTO db_snapshots(ts,engine,name,size,tables,conns) VALUES(?,?,?,?,?,?)",
        [(ts, d["engine"], d["name"], d.get("size"), d.get("tables"), d.get("connections"))
         for d in all_dbs if d.get("size") is not None])
    status, _ = dbs.mysql_status(cfg.get("mysql_defaults_file"))
    pg_status, _ = pg.status(cfg) if pg.enabled(cfg) else ({}, None)
    store.put_latest("databases", {"list": all_dbs, "mysql_status": status,
                                   "pg_status": pg_status, "error": err})
    ctx["databases"] = all_dbs
    ctx["mysql_status"] = status
    ctx["pg_status"] = pg_status


def task_ssl(ctx):
    cfg = ctx["cfg"]
    th = cfg.get("thresholds", {})
    out = {}
    ts = int(time.time())
    for dom in store.domains(cfg):
        info = ssl_check.check_domain(dom, th)
        out[dom["id"]] = info
        store.write(
            "INSERT INTO ssl_certs(ts,domain,issuer,subject,not_before,not_after,"
            "days_left,status,source) VALUES(?,?,?,?,?,?,?,?,?)",
            (ts, dom["domain"], info.get("issuer"), info.get("subject"),
             info.get("not_before"), info.get("not_after"), info.get("days_left"),
             info.get("status"), info.get("source")))
    # Certificates not tied to a domain: a certificate issued for an IP is
    # usually shared by several services, so its expiry breaks all of them.
    for extra in cfg.get("extra_certs", []):
        info = ssl_check.from_file(extra.get("path"))
        if extra.get("host"):
            live = ssl_check.from_live(extra["host"], extra.get("port", 443))
            if not live.get("error"):
                info = live
        info["domain"] = extra.get("name") or extra.get("host")
        info["note"] = extra.get("note")
        info["cert_path"] = extra.get("path")
        info["extra"] = True
        days = info.get("days_left")
        crit = th.get("ssl_critical_days", 8)
        warn = th.get("ssl_warning_days", 30)
        info["status"] = ("unknown" if days is None else "expired" if days < 0
                          else "critical" if days < crit else "warning" if days < warn else "ok")
        out["extra:" + (extra.get("name") or extra["path"])] = info
        store.write(
            "INSERT INTO ssl_certs(ts,domain,issuer,subject,not_before,not_after,"
            "days_left,status,source) VALUES(?,?,?,?,?,?,?,?,?)",
            (ts, info["domain"], info.get("issuer"), info.get("subject"),
             info.get("not_before"), info.get("not_after"), days,
             info.get("status"), info.get("source")))
    store.put_latest("ssl", out)
    ctx["ssl"] = out


def task_whois(ctx):
    """Domain registration expiry.

    Registries dislike frequent queries, so the interval is long and there is a
    pause between domains. A failed query does not overwrite the previous
    answer: yesterday's data with a note beats an empty field.
    """
    cfg = ctx["cfg"]
    th = cfg.get("thresholds", {})
    previous, prev_ts = store.get_latest("whois", {})
    previous = previous or {}
    # Restarting the unit runs this task again. The registry is shielded from
    # that here: a domain whose previous answer is still fresh is skipped.
    # The check is per domain, so a site added a minute ago is looked up right
    # away instead of waiting out the interval of the others.
    interval = cfg.get("intervals", {}).get("whois", 43200)
    fresh_until = time.time() - interval * 0.9
    out = {}
    doms = store.domains(cfg)
    asked = 0
    for dom in doms:
        known = previous.get(dom["id"])
        if known and (known.get("checked") or prev_ts or 0) > fresh_until:
            out[dom["id"]] = known
            continue
        if asked:
            time.sleep(3)
        asked += 1
        info = whois.lookup(dom["domain"], th)
        old = previous.get(dom["id"])
        if info.get("error") and old and old.get("paid_till"):
            stale = dict(old)
            stale["stale"] = True
            stale["last_error"] = info["error"]
            stale["checked"] = int(time.time())
            # recompute the remaining days from the stored date
            days = int((stale["paid_till"] - time.time()) // 86400)
            stale["days_left"] = days
            out[dom["id"]] = stale
            log("whois %s: %s, keeping the previous answer" % (dom["domain"], info["error"]))
            continue
        info["stale"] = False
        info["checked"] = int(time.time())
        out[dom["id"]] = info
        store.write(
            "INSERT INTO whois_info(ts,domain,registrar,created,paid_till,free_date,"
            "state,days_left,status,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (info["ts"], dom["domain"], info.get("registrar"), info.get("created"),
             info.get("paid_till"), info.get("free_date"), info.get("state"),
             info.get("days_left"), info.get("status"), info.get("error")))
    store.put_latest("whois", out)
    ctx["whois"] = out


def task_disk(ctx):
    cfg = ctx["cfg"]
    paths = list(cfg.get("storage_watch", []))
    for d in store.domains(cfg):
        if d.get("project_dir") and d["project_dir"] not in paths:
            paths.append(d["project_dir"])
    sizes = storage.dir_sizes(paths, timeout=120)
    ts = int(time.time())
    store.writemany("INSERT INTO disk_usage(ts,path,bytes) VALUES(?,?,?)",
                    [(ts, s["path"], s["bytes"]) for s in sizes if s["bytes"] is not None])
    store.put_latest("disk_dirs", sizes)
    ctx["disk_dirs"] = sizes


def task_alerts(ctx):
    cfg = ctx["cfg"]
    snapshot = _build_snapshot(ctx, cfg)
    active = alerts.evaluate(snapshot, cfg)
    alerts.sync(active, cfg)
    store.put_latest("alerts", active)


def _build_snapshot(ctx, cfg):
    hl = ctx.get("domain_health") or store.get_latest("domain_health", {})[0] or {}
    traffic = ctx.get("traffic") or store.get_latest("nginx_traffic", {})[0] or {}
    ssl_data = ctx.get("ssl") or store.get_latest("ssl", {})[0] or {}
    doms = []
    hour_ago = int(time.time()) - 3600
    for d in store.domains(cfg):
        row = store.one(
            "SELECT COALESCE(SUM(requests),0) r, COALESCE(SUM(c5xx),0) c5xx,"
            " COALESCE(SUM(c4xx),0) c4xx FROM domain_traffic WHERE domain=? AND ts>?",
            (d["domain"], hour_ago)) or {}
        entry = dict(d)
        entry.update(hl.get(d["id"], {}))
        entry["ssl"] = ssl_data.get(d["id"], {})
        entry["traffic_hour"] = {"requests": row.get("r", 0), "c5xx": row.get("c5xx", 0),
                                 "c4xx": row.get("c4xx", 0)}
        doms.append(entry)
    whois_data = ctx.get("whois") or store.get_latest("whois", {})[0] or {}
    for entry in doms:
        entry["whois"] = whois_data.get(entry["id"], {})
    return {
        "ssl_extra": {k: v for k, v in ssl_data.items() if k.startswith("extra:")},
        "system": ctx.get("system") or store.get_latest("system", {})[0] or {},
        "domains": doms,
        "services": ctx.get("services") or store.get_latest("services", [])[0] or [],
        "mysql_status": ctx.get("mysql_status") or
                        (store.get_latest("databases", {})[0] or {}).get("mysql_status", {}),
        "pg_status": ctx.get("pg_status") or
                     (store.get_latest("databases", {})[0] or {}).get("pg_status", {}),
    }


def task_rollup(ctx):
    """Roll up into hourly points and prune old rows: the dashboard must not
    grow its own database forever."""
    cfg = ctx["cfg"]
    ret = cfg.get("retention", {})
    hi = int(ret.get("highres_days", 7)) * 86400
    lo = int(ret.get("hourly_days", 180)) * 86400
    ev = int(ret.get("events_days", 365)) * 86400
    now = int(time.time())
    store.write(
        "INSERT OR REPLACE INTO sys_hourly(ts,cpu,ram_pct,disk_pct,load1,net_rx,net_tx,samples) "
        "SELECT (ts/3600)*3600, AVG(cpu), AVG(100.0*ram_used/NULLIF(ram_total,0)),"
        " AVG(100.0*disk_used/NULLIF(disk_total,0)), AVG(load1), AVG(net_rx), AVG(net_tx),"
        " COUNT(*) FROM sys_metrics WHERE ts > ? GROUP BY (ts/3600)", (now - 2 * 86400,))
    store.write("DELETE FROM sys_metrics WHERE ts < ?", (now - hi,))
    store.write("DELETE FROM sys_hourly WHERE ts < ?", (now - lo,))
    store.write("DELETE FROM domain_checks WHERE ts < ?", (now - 31 * 86400,))
    store.write("DELETE FROM domain_traffic WHERE ts < ?", (now - 31 * 86400,))
    store.write("DELETE FROM service_status WHERE ts < ?", (now - hi,))
    store.write("DELETE FROM recent_errors WHERE ts < ?", (now - 7 * 86400,))
    store.write("DELETE FROM events WHERE ts < ? AND resolved_ts IS NOT NULL", (now - ev,))
    store.write("DELETE FROM sessions WHERE expires < ?", (now,))


def bootstrap_history(ctx, only=None):
    """Rebuild traffic history from the logs already on disk.

    Runs on first start, and again for any domain added later: without it a
    site added today would show empty charts until tomorrow, even though its
    log has been filling up all along.
    """
    cfg = ctx["cfg"]
    since = int(time.time()) - 2 * 86400
    for dom in store.domains(cfg):
        if only is not None and dom["id"] not in only:
            continue
        path = dom.get("access_log")
        if not path:
            continue
        # "Already has history" means points older than an hour, not merely
        # a row: a site added minutes ago has rows from the ongoing collection
        # and would otherwise never get its past filled in.
        have = store.one(
            "SELECT COUNT(*) n FROM domain_traffic WHERE domain=? AND ts < ?",
            (dom["domain"], int(time.time()) - 3600))
        if have and have["n"] > 0:
            continue
        lines = nginxlog.scan_history(path, since, max_bytes=30 << 20)
        buckets = nginxlog.bucket_history(lines, bucket_sec=300, since=since)
        rows = [(ts + 300, dom["domain"], b["requests"], b["c2xx"], b["c3xx"],
                 b["c4xx"], b["c5xx"], b["bytes"], 300) for ts, b in sorted(buckets.items())]
        if rows:
            store.writemany(
                "INSERT INTO domain_traffic(ts,domain,requests,c2xx,c3xx,c4xx,c5xx,bytes,span)"
                " VALUES(?,?,?,?,?,?,?,?,?)", rows)
            log("history rebuilt from %s log: %d points" % (dom["domain"], len(rows)))
        # move the read position to the end so the same lines are not counted twice
        nginxlog.Tailer(path).read_new(max_bytes=1)


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    store.init()
    cfg = store.load_config()
    ctx = {"cfg": cfg, "tailers": {}}
    iv = cfg.get("intervals", {})
    log("srvmon %s collector started, domains monitored: %d"
        % (VERSION, len(store.domains(cfg))))
    bootstrap_history(ctx)

    tasks = [
        Task("system", task_system, iv.get("system", 20)),
        Task("processes", task_processes, iv.get("processes", 60), jitter=3),
        Task("domain_health", task_domain_health, iv.get("domain_health", 45), jitter=5),
        Task("nginx_logs", task_nginx_logs, iv.get("nginx_logs", 60), jitter=8),
        Task("services", task_services, iv.get("services", 60), jitter=11),
        Task("databases", task_databases, iv.get("databases", 300), jitter=14),
        Task("ssl", task_ssl, iv.get("ssl", 3600), jitter=17),
        Task("disk", task_disk, iv.get("disk_scan", 900), jitter=25),
        Task("whois", task_whois, iv.get("whois", 43200), jitter=35),
        Task("alerts", task_alerts, 60, jitter=30),
        Task("rollup", task_rollup, 3600, jitter=120),
    ]
    sysinfo.cpu_percent()  # the first sample only establishes a baseline
    sysinfo.network()
    sysinfo.processes(limit=1)
    known_domains = {d["id"] for d in store.domains(cfg)}
    # Tasks that must not wait out their interval when a site is added
    catch_up = {"ssl", "whois", "disk", "domain_health", "nginx_logs", "services"}

    while RUN:
        now = time.time()
        for t in tasks:
            if not RUN:
                break
            if t.due(now):
                t.run(ctx)
        # The config is re-read on the fly: a domain added through the page
        # is picked up without restarting the unit
        try:
            fresh = store.load_config()
            if fresh != ctx["cfg"]:
                ctx["cfg"] = fresh
                log("configuration reloaded")
                current = {d["id"] for d in store.domains(fresh)}
                added = current - known_domains
                known_domains = current
                if added:
                    # A newly added site should not look empty until the slow
                    # tasks come round: its log is read and its certificate,
                    # registration and size are collected right away.
                    log("new domains: %s — collecting now" % ", ".join(sorted(added)))
                    try:
                        bootstrap_history(ctx, only=added)
                    except Exception as exc:
                        log("history for new domains failed: %s" % exc)
                    for task in tasks:
                        if task.name in catch_up:
                            task.next_run = 0
        except (OSError, ValueError):
            pass
        time.sleep(2)
    log("collector stopped")


if __name__ == "__main__":
    main()
