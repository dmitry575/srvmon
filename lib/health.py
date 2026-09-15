"""Проверка доступности сайтов: DNS -> TCP -> HTTP/HTTPS.
Каждая ступень замеряется отдельно, чтобы в панели было видно не просто
«сайт лежит», а что именно сломалось."""
import http.client
import socket
import ssl
import time

UA = "srvmon/1.0 (+local server monitoring)"  # только latin-1: HTTP-заголовки кириллицу не принимают


def _dns(host, timeout=5):
    t0 = time.perf_counter()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1), "addresses": addrs}
    except socket.gaierror as exc:
        return {"ok": False, "ms": None, "code": "dns",
                "error": "DNS: %s" % (exc.strerror or str(exc))}
    finally:
        socket.setdefaulttimeout(None)


def _tcp(host, port, timeout=6):
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except socket.timeout:
        return {"ok": False, "ms": None, "code": "timeout", "error": "таймаут TCP"}
    except ConnectionRefusedError:
        return {"ok": False, "ms": None, "code": "refused", "error": "соединение отклонено"}
    except OSError as exc:
        return {"ok": False, "ms": None, "code": "net", "error": str(exc)[:120]}


def _http(host, path="/", https=True, timeout=10, port=None):
    t0 = time.perf_counter()
    conn = None
    try:
        if https:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port or 443, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port or 80, timeout=timeout)
        conn.request("GET", path or "/", headers={"User-Agent": UA, "Accept": "*/*",
                                                  "Connection": "close"})
        resp = conn.getresponse()
        body = resp.read(2048)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"ok": resp.status < 500, "status": resp.status, "ms": ms,
                "bytes": len(body), "location": resp.getheader("Location")}
    except ssl.SSLError as exc:
        return {"ok": False, "status": None, "ms": None, "code": "ssl",
                "error": "ошибка SSL: %s" % str(exc)[:120]}
    except socket.timeout:
        return {"ok": False, "status": None, "ms": None, "code": "timeout",
                "error": "таймаут HTTP"}
    except ConnectionRefusedError:
        return {"ok": False, "status": None, "ms": None, "code": "refused",
                "error": "соединение отклонено"}
    except (OSError, http.client.HTTPException) as exc:
        return {"ok": False, "status": None, "ms": None, "code": "net",
                "error": str(exc)[:120]}
    finally:
        if conn:
            try:
                conn.close()
            except OSError:
                pass


def check(dom, thresholds=None):
    th = thresholds or {}
    host = dom["domain"]
    path = dom.get("health_path") or "/"
    res = {"domain": host, "ts": int(time.time())}
    dns = _dns(host)
    res["dns"] = dns
    if not dns["ok"]:
        res.update({"ok": False, "state": "down", "error": dns.get("error"),
                    "error_code": "dns",
                    "resp_ms": None, "http_status": None, "https_status": None})
        return res
    tcp = _tcp(host, 443)
    res["tcp"] = tcp
    https = _http(host, path, https=True)
    http_plain = _http(host, path, https=False)
    res["https"] = https
    res["http"] = http_plain
    res["http_status"] = http_plain.get("status")
    res["https_status"] = https.get("status")
    res["resp_ms"] = https.get("ms") or http_plain.get("ms")
    status = https.get("status")
    err = https.get("error") or (None if status else http_plain.get("error"))
    res["error"] = err
    res["error_code"] = https.get("code") or http_plain.get("code")
    if status is None:
        res["ok"] = False
        res["state"] = "down"
    elif status >= 500:
        res["ok"] = False
        res["state"] = "down"
        res["error"] = "HTTP %d" % status
        res["error_code"] = "http_status"
        res["error_status"] = status
    elif status >= 400:
        res["ok"] = True
        res["state"] = "warning"
        res["error"] = "HTTP %d" % status
        res["error_code"] = "http_status"
        res["error_status"] = status
    else:
        res["ok"] = True
        res["state"] = "ok"
    warn_ms = th.get("response_warning_ms", 1500)
    crit_ms = th.get("response_critical_ms", 5000)
    if res["ok"] and res["resp_ms"]:
        if res["resp_ms"] >= crit_ms:
            res["state"] = "warning"
            res["error"] = "очень медленный ответ: %.0f мс" % res["resp_ms"]
            res["error_code"] = "very_slow"
        elif res["resp_ms"] >= warn_ms and res["state"] == "ok":
            res["state"] = "warning"
            res["error"] = "медленный ответ: %.0f мс" % res["resp_ms"]
            res["error_code"] = "slow"
    return res


def check_backend(port, path="/", timeout=6):
    """Отдельная проверка самого бэкенда на localhost — она отличает
    «упал сайт» от «упал nginx»."""
    if not port:
        return None
    t0 = time.perf_counter()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
        conn.request("GET", path, headers={"User-Agent": UA, "Connection": "close"})
        resp = conn.getresponse()
        resp.read(512)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        conn.close()
        return {"ok": resp.status < 500, "status": resp.status, "ms": ms}
    except (OSError, http.client.HTTPException) as exc:
        return {"ok": False, "status": None, "ms": None, "error": str(exc)[:120]}
