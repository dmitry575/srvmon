"""Certificates.

Both the file on disk and what port 443 actually serves are checked: a fresh
certificate may be sitting on disk while nginx still serves the old one.
"""
import os
import socket
import ssl
import time
from datetime import datetime, timezone

from .sysinfo import run


def _parse_openssl_date(raw):
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return int(datetime.strptime(raw.strip().replace("GMT", "").strip(),
                                         fmt.replace(" %Z", "")).replace(
                tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def from_file(path):
    if not path or not os.path.exists(path):
        return {"error": "certificate file not found", "source": "file", "path": path}
    code, out, err = run(["openssl", "x509", "-in", path, "-noout",
                          "-subject", "-issuer", "-startdate", "-enddate"], timeout=10)
    if code != 0:
        return {"error": err.strip()[:200] or "openssl could not read the file",
                "source": "file", "path": path}
    data = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        data[k.strip()] = v.strip()
    not_before = _parse_openssl_date(data.get("notBefore", ""))
    not_after = _parse_openssl_date(data.get("notAfter", ""))
    return _finish({
        "source": "file", "path": path,
        "subject": data.get("subject", ""), "issuer": _issuer_cn(data.get("issuer", "")),
        "not_before": not_before, "not_after": not_after,
    })


def _issuer_cn(raw):
    for part in raw.split(","):
        part = part.strip()
        if part.upper().startswith("CN"):
            return part.split("=", 1)[-1].strip()
    return raw.strip() or "unknown"


def from_live(host, port=443, timeout=8):
    """Live check: connect and take the certificate from the handshake."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                proto = tls.version()
    except (socket.gaierror, socket.timeout, OSError, ssl.SSLError) as exc:
        return {"error": str(exc)[:200], "source": "live", "host": host}
    not_after = None
    not_before = None
    if cert.get("notAfter"):
        not_after = int(ssl.cert_time_to_seconds(cert["notAfter"]))
    if cert.get("notBefore"):
        not_before = int(ssl.cert_time_to_seconds(cert["notBefore"]))
    issuer = ""
    for rdn in cert.get("issuer", ()):
        for k, v in rdn:
            if k == "organizationName" and not issuer:
                issuer = v
            if k == "commonName":
                issuer = issuer or v
    subject = ""
    for rdn in cert.get("subject", ()):
        for k, v in rdn:
            if k == "commonName":
                subject = v
    alt = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    return _finish({
        "source": "live", "host": host, "tls_version": proto,
        "subject": subject, "issuer": issuer or "unknown",
        "not_before": not_before, "not_after": not_after, "alt_names": alt,
    })


def _finish(info):
    now = time.time()
    if info.get("not_after"):
        days = int((info["not_after"] - now) // 86400)
        info["days_left"] = days
        if days < 0:
            info["status"] = "expired"
        elif days < 8:
            info["status"] = "critical"
        elif days < 30:
            info["status"] = "warning"
        else:
            info["status"] = "ok"
    else:
        info["days_left"] = None
        info["status"] = "unknown"
    return info


def check_domain(dom, thresholds=None):
    th = thresholds or {}
    warn = th.get("ssl_warning_days", 30)
    crit = th.get("ssl_critical_days", 8)
    live = from_live(dom["domain"])
    file_info = from_file(dom.get("ssl_cert")) if dom.get("ssl_cert") else {}
    best = live if not live.get("error") else file_info
    res = dict(best)
    res["domain"] = dom["domain"]
    res["file"] = file_info
    res["live_error"] = live.get("error")
    days = res.get("days_left")
    if days is None:
        res["status"] = "unknown"
    elif days < 0:
        res["status"] = "expired"
    elif days < crit:
        res["status"] = "critical"
    elif days < warn:
        res["status"] = "warning"
    else:
        res["status"] = "ok"
    return res
