"""Alert rules.

Each rule returns either a firing or None. Open events live in the database
and close themselves once the cause is gone — that is what makes the Alerts
page a timeline rather than a wall of repeats.

Delivery (Telegram, email, Discord) is not wired in here: notifiers attach
through a single dispatch function, so a channel is added without touching
any rule.
"""
import time

from . import store

SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}


def evaluate(snapshot, cfg):
    """snapshot is whatever the collector gathered. Returns active problems."""
    th = cfg.get("thresholds", {})
    out = []

    sysm = snapshot.get("system") or {}
    mem = sysm.get("memory") or {}
    disk = sysm.get("disk") or {}
    load = sysm.get("load") or {}

    if disk.get("percent") is not None:
        pct = disk["percent"]
        if pct >= th.get("disk_critical", 90):
            out.append(_a("disk", "/", "critical", "Disk is %.0f%% full" % pct,
                          tpl="disk.full", pct=round(pct)))
        elif pct >= th.get("disk_warning", 80):
            out.append(_a("disk", "/", "warning", "Disk is %.0f%% full" % pct,
                          tpl="disk.full", pct=round(pct)))

    if mem.get("percent") is not None:
        pct = mem["percent"]
        if pct >= th.get("ram_critical", 95):
            out.append(_a("ram", "memory", "critical", "Memory is %.0f%% used" % pct,
                          tpl="ram.high", pct=round(pct)))
        elif pct >= th.get("ram_warning", 85):
            out.append(_a("ram", "memory", "warning", "Memory is %.0f%% used" % pct,
                          tpl="ram.high", pct=round(pct)))
    if mem.get("swap_percent", 0) >= 95 and mem.get("swap_total"):
        out.append(_a("swap", "swap", "warning",
                      "Swap is completely used (%.0f%%)" % mem["swap_percent"],
                      tpl="swap.full", pct=round(mem["swap_percent"])))

    cores = load.get("cores") or 1
    if load.get("load1") is not None:
        per_core = load["load1"] / cores
        if per_core >= th.get("load_critical", 8.0) / max(cores, 1):
            pass
        if load["load1"] >= th.get("load_critical", 8.0):
            out.append(_a("load", "loadavg", "critical",
                          "Load average %.2f on %d cores" % (load["load1"], cores),
                          tpl="load.high", load=round(load["load1"], 2), cores=cores))
        elif load["load1"] >= th.get("load_warning", 4.0):
            out.append(_a("load", "loadavg", "warning",
                          "Load average %.2f on %d cores" % (load["load1"], cores),
                          tpl="load.high", load=round(load["load1"], 2), cores=cores))

    for fs in (sysm.get("filesystems") or []):
        if fs["mount"] == "/":
            continue
        if fs["percent"] >= th.get("disk_critical", 90) and fs["total"] > 1 << 30:
            out.append(_a("disk", fs["mount"], "critical",
                          "Filesystem %s is %.0f%% full" % (fs["mount"], fs["percent"]),
                          tpl="disk.mount_full", mount=fs["mount"], pct=round(fs["percent"])))

    for dom in snapshot.get("domains", []):
        name = dom.get("domain")
        hl = dom.get("health") or {}
        if hl.get("state") == "down":
            out.append(_a("domain.down", name, "critical",
                          "%s is down: %s" % (name, hl.get("error") or "no response"),
                          tpl="domain.down", domain=name,
                          reason=hl.get("error"), reason_code=hl.get("error_code")))
        elif hl.get("state") == "warning" and hl.get("error"):
            out.append(_a("domain.warn", name, "warning",
                          "%s: %s" % (name, hl["error"]),
                          tpl="domain.warn", domain=name, reason=hl.get("error"),
                          reason_code=hl.get("error_code"), ms=hl.get("resp_ms")))
        be = dom.get("backend") or {}
        if be and not be.get("ok"):
            out.append(_a("backend.down", name, "critical",
                          "Backend of %s (port %s) is not responding: %s" % (
                              name, dom.get("backend_port"),
                              be.get("error") or "HTTP %s" % be.get("status")),
                          tpl="backend.down", domain=name, port=dom.get("backend_port"),
                          reason=be.get("error") or "HTTP %s" % be.get("status")))
        ssl_i = dom.get("ssl") or {}
        days = ssl_i.get("days_left")
        if days is not None:
            if days < 0:
                out.append(_a("ssl", name, "critical", "Certificate for %s has expired" % name,
                              tpl="ssl.expired", domain=name))
            elif days < th.get("ssl_critical_days", 8):
                out.append(_a("ssl", name, "critical",
                              "Certificate for %s expires in %d days" % (name, days),
                              tpl="ssl.expiring", domain=name, days=days))
            elif days < th.get("ssl_warning_days", 30):
                out.append(_a("ssl", name, "warning",
                              "Certificate for %s expires in %d days" % (name, days),
                              tpl="ssl.expiring", domain=name, days=days))
        reg = dom.get("whois") or {}
        rdays = reg.get("days_left")
        if rdays is not None:
            if rdays < 0:
                out.append(_a("domain.expired", name, "critical",
                              "Domain registration for %s has expired" % name,
                              tpl="domain.expired", domain=name))
            elif rdays < th.get("domain_critical_days", 10):
                out.append(_a("domain.registration", name, "critical",
                              "Registration of %s expires in %d days" % (name, rdays),
                              tpl="domain.registration", domain=name, days=rdays))
            elif rdays < th.get("domain_warning_days", 30):
                out.append(_a("domain.registration", name, "warning",
                              "Registration of %s expires in %d days" % (name, rdays),
                              tpl="domain.registration", domain=name, days=rdays))

        tr = dom.get("traffic_hour") or {}
        c5 = tr.get("c5xx") or 0
        if c5 >= th.get("http5xx_critical_per_hour", 100):
            out.append(_a("http5xx", name, "critical",
                          "%s: %d 5xx responses in the last hour" % (name, c5),
                          tpl="http5xx", domain=name, count=c5))
        elif c5 >= th.get("http5xx_warning_per_hour", 20):
            out.append(_a("http5xx", name, "warning",
                          "%s: %d 5xx responses in the last hour" % (name, c5),
                          tpl="http5xx", domain=name, count=c5))

    for svc in snapshot.get("services", []):
        if svc.get("name", "").startswith("srvmon"):
            continue
        if svc.get("active") == "failed":
            out.append(_a("service.failed", svc["name"], "critical",
                          "Service %s failed (%s)" % (svc["name"], svc.get("result") or "failed"),
                          tpl="service.failed", service=svc["name"],
                          result=svc.get("result") or "failed"))
        elif svc.get("active") not in ("active", "activating"):
            out.append(_a("service.down", svc["name"], "warning",
                          "Service %s: state %s" % (svc["name"], svc.get("active")),
                          tpl="service.down", service=svc["name"], state=svc.get("active")))

    my = snapshot.get("mysql_status") or {}
    try:
        conn_n = int(my.get("Threads_connected", 0))
        maxc = int(my.get("max_connections", 151))
        if conn_n >= th.get("mysql_conn_critical", int(maxc * 0.9)):
            out.append(_a("mysql.conn", "mysql", "critical",
                          "MySQL: %d of %d connections in use" % (conn_n, maxc),
                          tpl="mysql.conn", used=conn_n, max=maxc))
        elif conn_n >= th.get("mysql_conn_warning", int(maxc * 0.66)):
            out.append(_a("mysql.conn", "mysql", "warning",
                          "MySQL: %d of %d connections in use" % (conn_n, maxc),
                          tpl="mysql.conn", used=conn_n, max=maxc))
    except (TypeError, ValueError):
        pass

    for key, info in (snapshot.get("ssl_extra") or {}).items():
        days = info.get("days_left")
        if days is None:
            continue
        name = info.get("domain") or key
        if days < 0:
            out.append(_a("ssl", name, "critical", "Certificate for %s has expired" % name,
                          tpl="ssl.expired", domain=name))
        elif days < th.get("ssl_critical_days", 8):
            out.append(_a("ssl", name, "critical",
                          "Certificate for %s expires in %d days — %s" % (
                              name, days, info.get("note") or ""),
                          tpl="ssl.expiring_shared", domain=name, days=days,
                          note=info.get("note")))
        elif days < th.get("ssl_warning_days", 30):
            out.append(_a("ssl", name, "warning",
                          "Certificate for %s expires in %d days" % (name, days),
                          tpl="ssl.expiring", domain=name, days=days))

    pgs = snapshot.get("pg_status") or {}
    if pgs.get("max_connections"):
        used, maxc = pgs.get("connections", 0), pgs["max_connections"]
        warn_at = th.get("pg_conn_warning", int(maxc * 0.7))
        crit_at = th.get("pg_conn_critical", int(maxc * 0.9))
        if used >= crit_at:
            out.append(_a("pg.conn", "postgres", "critical",
                          "PostgreSQL: %d of %d connections in use" % (used, maxc),
                          tpl="pg.conn", used=used, max=maxc))
        elif used >= warn_at:
            out.append(_a("pg.conn", "postgres", "warning",
                          "PostgreSQL: %d of %d connections in use" % (used, maxc),
                          tpl="pg.conn", used=used, max=maxc))
    if pgs.get("idle_in_transaction", 0) >= th.get("pg_idle_tx_warning", 3):
        out.append(_a("pg.idle_tx", "postgres", "warning",
                      "PostgreSQL: %d connections stuck idle in transaction"
                      % pgs["idle_in_transaction"],
                      tpl="pg.idle_tx", count=pgs["idle_in_transaction"]))

    for warn in growth_warnings(cfg):
        out.append(warn)

    out.sort(key=lambda a: SEV_ORDER.get(a["severity"], 3))
    return out


def growth_warnings(cfg):
    """Database growth is measured against our own snapshots: a week ago
    versus now."""
    th = cfg.get("thresholds", {})
    pct_limit = th.get("db_growth_warning_pct", 15)
    out = []
    week = int(time.time()) - 7 * 86400
    rows = store.rows(
        "SELECT engine, name, size, ts FROM db_snapshots WHERE ts > ? ORDER BY ts",
        (week - 86400,))
    by_db = {}
    for r in rows:
        by_db.setdefault((r["engine"], r["name"]), []).append(r)
    for (engine, name), series in by_db.items():
        if len(series) < 2:
            continue
        first, last = series[0], series[-1]
        if last["ts"] - first["ts"] < 2 * 86400:
            continue  # too little history to call it growth
        if not first["size"]:
            continue
        growth = 100.0 * (last["size"] - first["size"]) / first["size"]
        if growth >= pct_limit:
            out.append(_a("db.growth", "%s:%s" % (engine, name), "warning",
                          "Database %s grew by %.0f%% in %d days" % (
                              name, growth, max(1, (last["ts"] - first["ts"]) // 86400)),
                          tpl="db.growth", db=name, pct=round(growth),
                          days=max(1, (last["ts"] - first["ts"]) // 86400)))
    return out


def _a(kind, subject, severity, message, tpl=None, **params):
    """A firing carries both ready text and a structured form.

    The text is for the journal and for future notifications, where there is
    nobody to translate. The template id with parameters is for the page: it
    builds the phrase in the language the viewer picked, independent of the
    language the server speaks.
    """
    return {"kind": kind, "subject": subject, "severity": severity,
            "message": message, "tpl": tpl or kind, "params": params,
            "ts": int(time.time())}


def sync(active, cfg=None):
    """Reconcile active firings with open events in the database: a new one is
    opened, a vanished one is closed and recorded as recovered."""
    active_keys = {(a["kind"], a["subject"]) for a in active}
    for a in active:
        store.open_event(a["kind"], a["subject"], a["severity"], a["message"],
                         tpl=a.get("tpl"), params=a.get("params"))
    for ev in store.open_events():
        if ev["kind"].endswith(".recovered"):
            continue
        if (ev["kind"], ev["subject"]) not in active_keys:
            store.close_event(ev["kind"], ev["subject"])
    dispatch(active)
    return active


NOTIFIERS = []


def dispatch(active):
    """Extension point for Telegram, email or Discord: register a function
    that takes the list of firings. With no channel registered, an event simply
    stays in the database and shows up on the Alerts page."""
    for fn in NOTIFIERS:
        try:
            fn(active)
        except Exception:  # a delivery channel must never break metric collection
            pass
