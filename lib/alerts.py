"""Правила предупреждений.

Каждое правило возвращает либо срабатывание, либо None. Открытые события
хранятся в базе и закрываются сами, когда причина исчезла, — на странице
Alerts из этого складывается лента.

Доставка (Telegram, почта, Discord) сюда не зашита: notifier'ы подключаются
одной функцией dispatch, поэтому канал добавляется без правки правил.
"""
import time

from . import store

SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}


def evaluate(snapshot, cfg):
    """snapshot — то, что собрал коллектор. Возвращает список активных проблем."""
    th = cfg.get("thresholds", {})
    out = []

    sysm = snapshot.get("system") or {}
    mem = sysm.get("memory") or {}
    disk = sysm.get("disk") or {}
    load = sysm.get("load") or {}

    if disk.get("percent") is not None:
        pct = disk["percent"]
        if pct >= th.get("disk_critical", 90):
            out.append(_a("disk", "/", "critical", "Диск заполнен на %.0f%%" % pct,
                          tpl="disk.full", pct=round(pct)))
        elif pct >= th.get("disk_warning", 80):
            out.append(_a("disk", "/", "warning", "Диск заполнен на %.0f%%" % pct,
                          tpl="disk.full", pct=round(pct)))

    if mem.get("percent") is not None:
        pct = mem["percent"]
        if pct >= th.get("ram_critical", 95):
            out.append(_a("ram", "memory", "critical", "Память занята на %.0f%%" % pct,
                          tpl="ram.high", pct=round(pct)))
        elif pct >= th.get("ram_warning", 85):
            out.append(_a("ram", "memory", "warning", "Память занята на %.0f%%" % pct,
                          tpl="ram.high", pct=round(pct)))
    if mem.get("swap_percent", 0) >= 95 and mem.get("swap_total"):
        out.append(_a("swap", "swap", "warning",
                      "Подкачка занята полностью (%.0f%%)" % mem["swap_percent"],
                      tpl="swap.full", pct=round(mem["swap_percent"])))

    cores = load.get("cores") or 1
    if load.get("load1") is not None:
        per_core = load["load1"] / cores
        if per_core >= th.get("load_critical", 8.0) / max(cores, 1):
            pass
        if load["load1"] >= th.get("load_critical", 8.0):
            out.append(_a("load", "loadavg", "critical",
                          "Очередь задач %.2f при %d ядрах" % (load["load1"], cores),
                          tpl="load.high", load=round(load["load1"], 2), cores=cores))
        elif load["load1"] >= th.get("load_warning", 4.0):
            out.append(_a("load", "loadavg", "warning",
                          "Очередь задач %.2f при %d ядрах" % (load["load1"], cores),
                          tpl="load.high", load=round(load["load1"], 2), cores=cores))

    for fs in (sysm.get("filesystems") or []):
        if fs["mount"] == "/":
            continue
        if fs["percent"] >= th.get("disk_critical", 90) and fs["total"] > 1 << 30:
            out.append(_a("disk", fs["mount"], "critical",
                          "Раздел %s заполнен на %.0f%%" % (fs["mount"], fs["percent"]),
                          tpl="disk.mount_full", mount=fs["mount"], pct=round(fs["percent"])))

    for dom in snapshot.get("domains", []):
        name = dom.get("domain")
        hl = dom.get("health") or {}
        if hl.get("state") == "down":
            out.append(_a("domain.down", name, "critical",
                          "Сайт %s недоступен: %s" % (name, hl.get("error") or "нет ответа"),
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
                          "Бэкенд %s (порт %s) не отвечает: %s" % (
                              name, dom.get("backend_port"),
                              be.get("error") or "HTTP %s" % be.get("status")),
                          tpl="backend.down", domain=name, port=dom.get("backend_port"),
                          reason=be.get("error") or "HTTP %s" % be.get("status")))
        ssl_i = dom.get("ssl") or {}
        days = ssl_i.get("days_left")
        if days is not None:
            if days < 0:
                out.append(_a("ssl", name, "critical", "Сертификат %s просрочен" % name,
                              tpl="ssl.expired", domain=name))
            elif days < th.get("ssl_critical_days", 8):
                out.append(_a("ssl", name, "critical",
                              "Сертификат %s истекает через %d дн." % (name, days),
                              tpl="ssl.expiring", domain=name, days=days))
            elif days < th.get("ssl_warning_days", 30):
                out.append(_a("ssl", name, "warning",
                              "Сертификат %s истекает через %d дн." % (name, days),
                              tpl="ssl.expiring", domain=name, days=days))
        reg = dom.get("whois") or {}
        rdays = reg.get("days_left")
        if rdays is not None:
            if rdays < 0:
                out.append(_a("domain.expired", name, "critical",
                              "Регистрация домена %s истекла" % name,
                              tpl="domain.expired", domain=name))
            elif rdays < th.get("domain_critical_days", 10):
                out.append(_a("domain.registration", name, "critical",
                              "Регистрация %s заканчивается через %d дн." % (name, rdays),
                              tpl="domain.registration", domain=name, days=rdays))
            elif rdays < th.get("domain_warning_days", 30):
                out.append(_a("domain.registration", name, "warning",
                              "Регистрация %s заканчивается через %d дн." % (name, rdays),
                              tpl="domain.registration", domain=name, days=rdays))

        tr = dom.get("traffic_hour") or {}
        c5 = tr.get("c5xx") or 0
        if c5 >= th.get("http5xx_critical_per_hour", 100):
            out.append(_a("http5xx", name, "critical",
                          "%s: %d ответов 5xx за час" % (name, c5),
                          tpl="http5xx", domain=name, count=c5))
        elif c5 >= th.get("http5xx_warning_per_hour", 20):
            out.append(_a("http5xx", name, "warning",
                          "%s: %d ответов 5xx за час" % (name, c5),
                          tpl="http5xx", domain=name, count=c5))

    for svc in snapshot.get("services", []):
        if svc.get("name", "").startswith("srvmon"):
            continue
        if svc.get("active") == "failed":
            out.append(_a("service.failed", svc["name"], "critical",
                          "Служба %s упала (%s)" % (svc["name"], svc.get("result") or "failed"),
                          tpl="service.failed", service=svc["name"],
                          result=svc.get("result") or "failed"))
        elif svc.get("active") not in ("active", "activating"):
            out.append(_a("service.down", svc["name"], "warning",
                          "Служба %s: состояние %s" % (svc["name"], svc.get("active")),
                          tpl="service.down", service=svc["name"], state=svc.get("active")))

    my = snapshot.get("mysql_status") or {}
    try:
        conn_n = int(my.get("Threads_connected", 0))
        maxc = int(my.get("max_connections", 151))
        if conn_n >= th.get("mysql_conn_critical", int(maxc * 0.9)):
            out.append(_a("mysql.conn", "mysql", "critical",
                          "MySQL: %d соединений из %d" % (conn_n, maxc),
                          tpl="mysql.conn", used=conn_n, max=maxc))
        elif conn_n >= th.get("mysql_conn_warning", int(maxc * 0.66)):
            out.append(_a("mysql.conn", "mysql", "warning",
                          "MySQL: %d соединений из %d" % (conn_n, maxc),
                          tpl="mysql.conn", used=conn_n, max=maxc))
    except (TypeError, ValueError):
        pass

    for key, info in (snapshot.get("ssl_extra") or {}).items():
        days = info.get("days_left")
        if days is None:
            continue
        name = info.get("domain") or key
        if days < 0:
            out.append(_a("ssl", name, "critical", "Сертификат %s просрочен" % name,
                          tpl="ssl.expired", domain=name))
        elif days < th.get("ssl_critical_days", 8):
            out.append(_a("ssl", name, "critical",
                          "Сертификат %s истекает через %d дн. — %s" % (
                              name, days, info.get("note") or ""),
                          tpl="ssl.expiring_shared", domain=name, days=days,
                          note=info.get("note")))
        elif days < th.get("ssl_warning_days", 30):
            out.append(_a("ssl", name, "warning",
                          "Сертификат %s истекает через %d дн." % (name, days),
                          tpl="ssl.expiring", domain=name, days=days))

    for warn in growth_warnings(cfg):
        out.append(warn)

    out.sort(key=lambda a: SEV_ORDER.get(a["severity"], 3))
    return out


def growth_warnings(cfg):
    """Рост баз считаем по собственным снимкам: неделю назад против сейчас."""
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
            continue  # слишком короткая история, чтобы говорить о росте
        if not first["size"]:
            continue
        growth = 100.0 * (last["size"] - first["size"]) / first["size"]
        if growth >= pct_limit:
            out.append(_a("db.growth", "%s:%s" % (engine, name), "warning",
                          "База %s выросла на %.0f%% за %d дн." % (
                              name, growth, max(1, (last["ts"] - first["ts"]) // 86400)),
                          tpl="db.growth", db=name, pct=round(growth),
                          days=max(1, (last["ts"] - first["ts"]) // 86400)))
    return out


def _a(kind, subject, severity, message, tpl=None, **params):
    """Срабатывание несёт и готовый текст, и разобранный вид.

    Текст нужен журналу и будущим уведомлениям, где переводить некому.
    Идентификатор шаблона с параметрами нужен странице: она собирает фразу
    на том языке, который выбрал смотрящий, и не зависит от языка сервера.
    """
    return {"kind": kind, "subject": subject, "severity": severity,
            "message": message, "tpl": tpl or kind, "params": params,
            "ts": int(time.time())}


def sync(active, cfg=None):
    """Сверяет активные срабатывания с открытыми событиями в базе:
    новое — открывает, исчезнувшее — закрывает и пишет «восстановлено»."""
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
    """Точка расширения под Telegram/почту/Discord: регистрируется функция,
    принимающая список срабатываний. Сейчас каналов нет — событие просто
    остаётся в базе и видно на странице Alerts."""
    for fn in NOTIFIERS:
        try:
            fn(active)
        except Exception:  # канал доставки не должен ронять сбор метрик
            pass
