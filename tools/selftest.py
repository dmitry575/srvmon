#!/usr/bin/env python3
"""Проверка панели целиком: вход, все разделы API и наличие полей,
на которые опирается интерфейс. Запускать после изменений."""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SRVMON_URL", "http://127.0.0.1:8452")
LOGIN = os.environ.get("SRVMON_LOGIN", "admin")
PASSWORD = os.environ.get("SRVMON_PASSWORD", "")

ok_count = fail_count = 0
cookie = None


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print("  OK   %-46s %s" % (name, detail))
    else:
        fail_count += 1
        print("  СБОЙ %-46s %s" % (name, detail))


def req(path, method="GET", body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode()), resp.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode()), e.headers
        except ValueError:
            return e.code, {}, e.headers


def main():
    global cookie
    print("Проверка панели:", BASE)
    print("\n[ доступ ]")
    code, _, _ = req("/api/overview")
    check("API без входа закрыт", code == 401, "код %d" % code)
    code, data, hdrs = req("/api/login", "POST", {"login": LOGIN, "password": "неверный"})
    check("неверный пароль отклоняется", code == 401)
    code, data, hdrs = req("/api/login", "POST", {"login": LOGIN, "password": PASSWORD})
    check("вход по паролю", code == 200 and data.get("ok"))
    sc = hdrs.get("Set-Cookie", "")
    cookie = sc.split(";")[0]
    check("cookie помечена HttpOnly", "HttpOnly" in sc)
    check("cookie помечена SameSite", "SameSite" in sc)

    print("\n[ разделы ]")
    code, ov, _ = req("/api/overview")
    check("обзор", code == 200 and "system" in ov)
    sysd = ov.get("system", {})
    check("метрики процессора", isinstance(sysd.get("cpu"), (int, float)), "%.1f%%" % (sysd.get("cpu") or 0))
    check("метрики памяти", sysd.get("memory", {}).get("total", 0) > 0,
          "%.1f%%" % sysd.get("memory", {}).get("percent", 0))
    check("метрики диска", sysd.get("disk", {}).get("total", 0) > 0,
          "%.1f%%" % sysd.get("disk", {}).get("percent", 0))
    check("очередь задач", sysd.get("load", {}).get("load1") is not None,
          str(sysd.get("load", {}).get("load1")))
    check("сеть", sysd.get("network", {}).get("rx_total", 0) > 0)
    check("аптайм", sysd.get("uptime", 0) > 0, "%d ч" % (sysd.get("uptime", 0) / 3600))
    check("список доменов в обзоре", len(ov.get("domains", [])) == 2,
          "%d шт." % len(ov.get("domains", [])))
    for d in ov.get("domains", []):
        check("  домен %s" % d["domain"], d.get("state") in ("ok", "warning", "down"),
              "состояние %s, %s мс, запросов 24ч %s" % (d.get("state"), d.get("resp_ms"),
                                                        d.get("requests_24h")))
        check("  трафик %s не нулевой" % d["domain"], (d.get("requests_24h") or 0) > 0)
        check("  ssl %s" % d["domain"], d.get("ssl_days") is not None,
              "%s дней" % d.get("ssl_days"))
        check("  объём %s" % d["domain"], (d.get("disk") or 0) > 0, "%s байт" % d.get("disk"))

    code, doms, _ = req("/api/domains")
    check("раздел доменов", code == 200 and len(doms.get("domains", [])) == 2)
    for d in doms["domains"]:
        code, dd, _ = req("/api/domains/" + d["id"])
        check("карточка %s" % d["domain"], code == 200 and dd.get("health"))
        check("  история трафика", len(dd.get("traffic", {}).get("series", [])) > 0,
              "%d точек" % len(dd.get("traffic", {}).get("series", [])))
        check("  бэкенд-служба", (dd.get("service") or {}).get("active") == "active",
              (dd.get("service") or {}).get("name", ""))
        check("  аптайм посчитан", dd.get("availability", {}).get("uptime_24h") is not None,
              "%s%%" % dd.get("availability", {}).get("uptime_24h"))

    code, dbs_, _ = req("/api/databases")
    check("раздел баз", code == 200 and len(dbs_.get("databases", [])) > 0,
          "%d баз" % len(dbs_.get("databases", [])))
    check("  статус mysql", bool(dbs_.get("mysql_status", {}).get("version")),
          dbs_.get("mysql_status", {}).get("version", ""))
    my = [d for d in dbs_["databases"] if d["engine"] == "mysql" and not d.get("system")]
    sq = [d for d in dbs_["databases"] if d["engine"] == "sqlite"]
    check("  базы mysql найдены", len(my) > 0, ", ".join(d["name"] for d in my))
    check("  базы sqlite найдены", len(sq) > 0, ", ".join(d["name"] for d in sq))
    for d in (my[:1] + sq[:1]):
        code, dd, _ = req("/api/databases/" + d["id"])
        check("  карточка базы %s" % d["name"], code == 200 and dd.get("size"),
              "%s таблиц, топ: %s" % (dd.get("tables"),
                                      (dd.get("tables_list") or [{}])[0].get("name")))

    code, st, _ = req("/api/storage")
    check("раздел диска", code == 200 and len(st.get("filesystems", [])) > 0)
    check("  каталоги измерены", len([d for d in st.get("directories", []) if d.get("bytes")]) > 3,
          "%d каталогов" % len(st.get("directories", [])))
    code, br, _ = req("/api/browse?path=/root")
    check("  просмотр каталога", code == 200 and br.get("entries"))
    code, br2, _ = req("/api/browse?path=/etc/shadow")
    check("  выход за пределы запрещён", bool(br2.get("error")))
    code, br3, _ = req("/api/browse?path=/root/../etc")
    check("  обход через .. запрещён", bool(br3.get("error")))

    code, sv, _ = req("/api/services")
    check("раздел служб", code == 200 and len(sv.get("services", [])) > 5,
          "%d служб" % len(sv.get("services", [])))
    running = [s for s in sv["services"] if s.get("active") == "active"]
    check("  службы работают", len(running) >= 10, "%d активных" % len(running))
    check("  порты привязаны к службам", any(s.get("ports") for s in sv["services"]))

    code, pr, _ = req("/api/processes")
    check("раздел процессов", code == 200 and len(pr.get("processes", [])) > 10,
          "%d процессов" % len(pr.get("processes", [])))
    check("  секреты в командах скрыты",
          not any("password=" in (p.get("cmd") or "").lower() and "***" not in p.get("cmd", "")
                  for p in pr["processes"]))

    code, nw, _ = req("/api/network")
    check("раздел портов", code == 200 and len(nw.get("ports", [])) > 5,
          "%d портов" % len(nw.get("ports", [])))
    real = [p for p in nw["ports"] if not p.get("ephemeral")]
    pub = [p for p in real if p["scope"] == "public"]
    check("  временные UDP помечены", len(real) < len(nw["ports"]),
          "%d временных" % (len(nw["ports"]) - len(real)))
    check("  публичные и локальные разделены", 0 < len(pub) < len(real),
          "%d наружу из %d" % (len(pub), len(real)))
    check("  панель слушает только localhost",
          all(p["scope"] == "localhost" for p in nw["ports"] if p["port"] == 8452))

    code, ss_, _ = req("/api/ssl")
    certs = ss_.get("certificates", [])
    check("раздел сертификатов", code == 200 and len(certs) >= 2, "%d шт." % len(certs))
    check("  сертификат на IP под наблюдением",
          any(c.get("extra") for c in certs),
          next((c["domain"] for c in certs if c.get("extra")), "не найден"))
    for c in ss_["certificates"]:
        check("  сертификат %s" % c["domain"], c.get("days_left") is not None,
              "%s, %s дней, %s" % (c.get("issuer"), c.get("days_left"), c.get("status")))

    print("\n[ сроки регистрации ]")
    code, doms2, _ = req("/api/domains")
    for d in doms2["domains"]:
        reg = d.get("registration") or {}
        check("регистрация %s" % d["domain"], reg.get("days_left") is not None,
              "до %s, осталось %s дн., %s" % (reg.get("paid_till_raw") or "—",
                                              reg.get("days_left"), reg.get("registrar")))

    code, al, _ = req("/api/alerts")
    check("раздел событий", code == 200 and "active" in al,
          "%d активных, %d в ленте" % (len(al.get("active", [])), len(al.get("events", []))))

    code, mt, _ = req("/api/metrics?range=24h")
    check("метрики для графиков", code == 200 and len(mt.get("system", [])) > 0,
          "%d системных точек" % len(mt.get("system", [])))
    check("  метрики по доменам", all(len(v) > 0 for v in mt.get("domains", {}).values()),
          ", ".join("%s: %d" % (k, len(v)) for k, v in mt.get("domains", {}).items()))

    code, se, _ = req("/api/settings")
    check("раздел настроек", code == 200 and se.get("thresholds"))

    print("\n[ защита от изменений ]")
    code, r1, _ = req("/api/settings", "POST", {"domain": {"id": "плохой id", "domain": "x"}})
    check("неверный идентификатор отклонён", bool(r1.get("error")))
    code, r2, _ = req("/api/settings", "POST",
                      {"domain": {"id": "test", "domain": "не домен!"}})
    check("неверное имя домена отклонено", bool(r2.get("error")))
    code, r3, _ = req("/api/nonexistent")
    check("неизвестный путь 404", code == 404)

    print("\n[ статика ]")
    for f in ("/", "/app.js", "/style.css"):
        try:
            rq = urllib.request.Request(BASE + f)
            with urllib.request.urlopen(rq, timeout=10) as resp:
                check("файл %s" % f, resp.status == 200, "%d байт" % len(resp.read()))
        except urllib.error.HTTPError as e:
            check("файл %s" % f, False, "код %d" % e.code)

    print("\nИтог: успешно %d, сбоев %d" % (ok_count, fail_count))
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
