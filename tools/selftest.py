#!/usr/bin/env python3
"""End-to-end check of the dashboard: sign-in, every API section, and the
presence of the fields the interface relies on. Run after any change."""
import json
import os
import sys
import urllib.error
import urllib.parse
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
        print("  FAIL %-46s %s" % (name, detail))


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
    print("Checking dashboard:", BASE)
    print("\n[ access ]")
    code, _, _ = req("/api/overview")
    check("API is closed without a session", code == 401, "code %d" % code)
    code, data, hdrs = req("/api/login", "POST", {"login": LOGIN, "password": "wrong-password"})
    check("wrong password is rejected", code == 401)
    code, data, hdrs = req("/api/login", "POST", {"login": LOGIN, "password": PASSWORD})
    check("sign-in with the password", code == 200 and data.get("ok"))
    sc = hdrs.get("Set-Cookie", "")
    cookie = sc.split(";")[0]
    check("cookie is marked HttpOnly", "HttpOnly" in sc)
    check("cookie is marked SameSite", "SameSite" in sc)

    print("\n[ sections ]")
    code, ov, _ = req("/api/overview")
    check("overview", code == 200 and "system" in ov)
    sysd = ov.get("system", {})
    check("cpu metrics", isinstance(sysd.get("cpu"), (int, float)), "%.1f%%" % (sysd.get("cpu") or 0))
    check("memory metrics", sysd.get("memory", {}).get("total", 0) > 0,
          "%.1f%%" % sysd.get("memory", {}).get("percent", 0))
    check("disk metrics", sysd.get("disk", {}).get("total", 0) > 0,
          "%.1f%%" % sysd.get("disk", {}).get("percent", 0))
    check("load average", sysd.get("load", {}).get("load1") is not None,
          str(sysd.get("load", {}).get("load1")))
    check("network", sysd.get("network", {}).get("rx_total", 0) > 0)
    check("uptime", sysd.get("uptime", 0) > 0, "%d h" % (sysd.get("uptime", 0) / 3600))
    # How many domains there are depends on the installation; what matters is
    # that the overview and the domains section agree and that each is alive.
    check("domain list in the overview", len(ov.get("domains", [])) >= 1,
          "%d" % len(ov.get("domains", [])))
    for d in ov.get("domains", []):
        check("  domain %s" % d["domain"], d.get("state") in ("ok", "warning", "down"),
              "state %s, %s ms, requests 24h %s" % (d.get("state"), d.get("resp_ms"),
                                                        d.get("requests_24h")))
        check("  traffic for %s is non-zero" % d["domain"], (d.get("requests_24h") or 0) > 0)
        check("  ssl %s" % d["domain"], d.get("ssl_days") is not None,
              "%s days" % d.get("ssl_days"))
        check("  size of %s" % d["domain"], (d.get("disk") or 0) > 0, "%s bytes" % d.get("disk"))

    code, doms, _ = req("/api/domains")
    check("domains section", code == 200
          and len(doms.get("domains", [])) >= len(ov.get("domains", [])),
          "%d domains" % len(doms.get("domains", [])))
    for d in doms["domains"]:
        code, dd, _ = req("/api/domains/" + d["id"])
        check("domain page %s" % d["domain"], code == 200 and dd.get("health"))
        check("  traffic history", len(dd.get("traffic", {}).get("series", [])) > 0,
              "%d points" % len(dd.get("traffic", {}).get("series", [])))
        check("  backend unit", (dd.get("service") or {}).get("active") == "active",
              (dd.get("service") or {}).get("name", ""))
        check("  uptime computed", dd.get("availability", {}).get("uptime_24h") is not None,
              "%s%%" % dd.get("availability", {}).get("uptime_24h"))

    code, dbs_, _ = req("/api/databases")
    check("databases section", code == 200 and len(dbs_.get("databases", [])) > 0,
          "%d databases" % len(dbs_.get("databases", [])))
    check("  mysql status", bool(dbs_.get("mysql_status", {}).get("version")),
          dbs_.get("mysql_status", {}).get("version", ""))
    my = [d for d in dbs_["databases"] if d["engine"] == "mysql" and not d.get("system")]
    sq = [d for d in dbs_["databases"] if d["engine"] == "sqlite"]
    pgs = [d for d in dbs_["databases"] if d["engine"] == "postgres" and not d.get("system")]
    check("  mysql databases found", len(my) > 0, ", ".join(d["name"] for d in my))
    check("  sqlite databases found", len(sq) > 0, ", ".join(d["name"] for d in sq))
    if dbs_.get("pg_status"):
        st = dbs_["pg_status"]
        check("  postgresql responds", bool(st.get("version")), st.get("version", ""))
        check("  postgresql databases found", len(pgs) > 0,
              ", ".join(d["name"] for d in pgs) or "no user databases")
        check("  postgresql connections", st.get("max_connections", 0) > 0,
              "%s of %s" % (st.get("connections"), st.get("max_connections")))
    else:
        print("  ---  postgresql not configured, section skipped")
    for d in (my[:1] + sq[:1] + pgs[:1]):
        code, dd, _ = req("/api/databases/" + d["id"])
        top = (dd.get("tables_list") or [{}])[0]
        check("  database page %s (%s)" % (d["name"], d["engine"]),
              code == 200 and dd.get("size"),
              "%s tables, top: %s" % (dd.get("tables"), top.get("name")))
        if d["engine"] == "postgres":
            check("    size is broken down",
                  dd.get("table_size") is not None and dd.get("index_size") is not None,
                  "data %s, indexes %s, TOAST %s" % (
                      dd.get("table_size"), dd.get("index_size"), dd.get("toast_size")))
            # An empty value is a valid state: PostgreSQL loses these counters
            # when the server restarts uncleanly, and a fresh table has never
            # been vacuumed. What matters is that the fields are reported.
            tables = dd.get("tables_list", [])
            check("    vacuum and analyze fields are reported",
                  bool(tables) and all("last_vacuum" in x and "last_analyze" in x
                                       for x in tables),
                  "with a time: %d of %d" % (
                      sum(1 for x in tables if x.get("last_vacuum") or x.get("last_analyze")),
                      len(tables)))
            check("    largest indexes retrieved", len(dd.get("indexes", [])) > 0,
                  ", ".join(i["name"] for i in dd.get("indexes", [])[:2]))

    code, st, _ = req("/api/storage")
    check("storage section", code == 200 and len(st.get("filesystems", [])) > 0)
    check("  directories measured", len([d for d in st.get("directories", []) if d.get("bytes")]) > 3,
          "%d directories" % len(st.get("directories", [])))
    # The path comes from the installation itself: hard-coding one would tie
    # the test to a particular machine and leak its layout into the repository.
    browse_root = (st.get("allowed_roots") or ["/"])[0]
    code, br, _ = req("/api/browse?path=" + urllib.parse.quote(browse_root))
    check("  directory listing", code == 200 and br.get("entries"), browse_root)
    code, br2, _ = req("/api/browse?path=/etc/shadow")
    check("  escaping the allowlist is refused", bool(br2.get("error")))
    code, br3, _ = req("/api/browse?path=/root/../etc")
    check("  traversal through .. is refused", bool(br3.get("error")))

    code, sv, _ = req("/api/services")
    check("services section", code == 200 and len(sv.get("services", [])) > 5,
          "%d services" % len(sv.get("services", [])))
    running = [s for s in sv["services"] if s.get("active") == "active"]
    check("  services are running", len(running) >= 10, "%d active" % len(running))
    check("  ports are matched to services", any(s.get("ports") for s in sv["services"]))

    code, pr, _ = req("/api/processes")
    check("processes section", code == 200 and len(pr.get("processes", [])) > 10,
          "%d processes" % len(pr.get("processes", [])))
    check("  secrets in command lines are masked",
          not any("password=" in (p.get("cmd") or "").lower() and "***" not in p.get("cmd", "")
                  for p in pr["processes"]))

    code, nw, _ = req("/api/network")
    check("network section", code == 200 and len(nw.get("ports", [])) > 5,
          "%d ports" % len(nw.get("ports", [])))
    real = [p for p in nw["ports"] if not p.get("ephemeral")]
    pub = [p for p in real if p["scope"] == "public"]
    check("  transient UDP sockets are flagged", len(real) < len(nw["ports"]),
          "%d transient" % (len(nw["ports"]) - len(real)))
    check("  public and local are separated", 0 < len(pub) < len(real),
          "%d public of %d" % (len(pub), len(real)))
    check("  the dashboard listens on localhost only",
          all(p["scope"] == "localhost" for p in nw["ports"] if p["port"] == 8452))

    code, ss_, _ = req("/api/ssl")
    certs = ss_.get("certificates", [])
    check("certificates section", code == 200 and len(certs) >= 1, "%d" % len(certs))
    check("  the IP certificate is monitored",
          any(c.get("extra") for c in certs),
          next((c["domain"] for c in certs if c.get("extra")), "not found"))
    for c in ss_["certificates"]:
        check("  certificate %s" % c["domain"], c.get("days_left") is not None,
              "%s, %s days, %s" % (c.get("issuer"), c.get("days_left"), c.get("status")))

    print("\n[ domain registration ]")
    code, doms2, _ = req("/api/domains")
    for d in doms2["domains"]:
        reg = d.get("registration") or {}
        check("registration of %s" % d["domain"], reg.get("days_left") is not None,
              "until %s, %s days left, %s" % (reg.get("paid_till_raw") or "—",
                                              reg.get("days_left"), reg.get("registrar")))

    code, al, _ = req("/api/alerts")
    check("events section", code == 200 and "active" in al,
          "%d active, %d in the timeline" % (len(al.get("active", [])), len(al.get("events", []))))

    code, mt, _ = req("/api/metrics?range=24h")
    check("metrics for charts", code == 200 and len(mt.get("system", [])) > 0,
          "%d system points" % len(mt.get("system", [])))
    check("  per-domain metrics", all(len(v) > 0 for v in mt.get("domains", {}).values()),
          ", ".join("%s: %d" % (k, len(v)) for k, v in mt.get("domains", {}).items()))

    code, se, _ = req("/api/settings")
    check("settings section", code == 200 and se.get("thresholds"))

    print("\n[ write protection ]")
    code, r1, _ = req("/api/settings", "POST", {"domain": {"id": "bad id", "domain": "x"}})
    check("invalid identifier is rejected", bool(r1.get("error")))
    code, r2, _ = req("/api/settings", "POST",
                      {"domain": {"id": "test", "domain": "not a domain!"}})
    check("invalid domain name is rejected", bool(r2.get("error")))
    code, r3, _ = req("/api/nonexistent")
    check("unknown endpoint returns 404", code == 404)

    print("\n[ static files ]")
    for f in ("/", "/app.js", "/style.css"):
        try:
            rq = urllib.request.Request(BASE + f)
            with urllib.request.urlopen(rq, timeout=10) as resp:
                check("file %s" % f, resp.status == 200, "%d bytes" % len(resp.read()))
        except urllib.error.HTTPError as e:
            check("file %s" % f, False, "code %d" % e.code)

    print("\nResult: %d passed, %d failed" % (ok_count, fail_count))
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
