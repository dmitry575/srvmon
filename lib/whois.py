"""Срок регистрации домена.

Запрашиваем реестр напрямую по порту 43 — внешняя утилита не нужна, а
форматы ответов у зон разные, и разбирать их всё равно пришлось бы самим.
Для .ru/.su/.рф сервер известен заранее; для прочих зон он выясняется
у IANA и запоминается в конфиге сессии.

Реестры не любят частых обращений, поэтому коллектор ходит сюда раз в
двенадцать часов, а между доменами делает паузу. Если запрос не удался,
показываются последние удачные данные с пометкой, а не пустота.
"""
import re
import socket
import time
from calendar import timegm

DIRECT = {
    "ru": "whois.tcinet.ru", "su": "whois.tcinet.ru", "xn--p1ai": "whois.tcinet.ru",
    "com": "whois.verisign-grs.com", "net": "whois.verisign-grs.com",
    "org": "whois.pir.org", "io": "whois.nic.io", "dev": "whois.nic.google",
}

# Поле с датой окончания называется по-разному в каждом реестре.
EXPIRY_KEYS = (
    "paid-till", "registry expiry date", "expiry date", "expiration date",
    "registrar registration expiration date", "expires", "expire",
    "domain expiration date", "renewal date",
)
CREATED_KEYS = ("created", "creation date", "registered", "registration time")
REGISTRAR_KEYS = ("registrar", "sponsoring registrar", "registrar name")
STATE_KEYS = ("state", "domain status", "status")

_server_cache = {}


def _query(server, request, timeout=12):
    with socket.create_connection((server, 43), timeout=timeout) as sock:
        sock.sendall((request + "\r\n").encode("utf-8", "replace"))
        chunks = []
        while True:
            data = sock.recv(8192)
            if not data:
                break
            chunks.append(data)
            if sum(len(c) for c in chunks) > 256 * 1024:
                break
    return b"".join(chunks).decode("utf-8", "replace")


def _server_for(domain):
    zone = domain.rsplit(".", 1)[-1].lower()
    if zone in DIRECT:
        return DIRECT[zone]
    if zone in _server_cache:
        return _server_cache[zone]
    try:
        text = _query("whois.iana.org", zone)
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "whois" and value.strip():
            _server_cache[zone] = value.strip()
            return _server_cache[zone]
    return None


def _parse_date(raw):
    raw = raw.strip()
    if not raw:
        return None
    # 2027-09-09T16:30:01Z, 2027-09-09 16:30:01, 09.09.2027, 09-Sep-2027
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ]?(\d{2})?:?(\d{2})?:?(\d{2})?", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        try:
            return timegm((y, mo, d, hh, mm, ss, 0, 0, 0))
        except (ValueError, OverflowError):
            return None
    m = re.match(r"(\d{2})[./-](\d{2})[./-](\d{4})", raw)
    if m:
        try:
            return timegm((int(m.group(3)), int(m.group(2)), int(m.group(1)), 0, 0, 0, 0, 0, 0))
        except (ValueError, OverflowError):
            return None
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", raw)
    if m:
        months = {mn: i for i, mn in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
        mo = months.get(m.group(2).lower())
        if mo:
            try:
                return timegm((int(m.group(3)), mo, int(m.group(1)), 0, 0, 0, 0, 0, 0))
            except (ValueError, OverflowError):
                return None
    return None


def _collect(text):
    fields = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("%", "#", ">>>")):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        fields.setdefault(key, []).append(value)
    return fields


def lookup(domain, thresholds=None):
    """Возвращает срок регистрации и сопутствующие данные по домену."""
    th = thresholds or {}
    warn = th.get("domain_warning_days", 30)
    crit = th.get("domain_critical_days", 10)
    res = {"domain": domain, "ts": int(time.time()), "error": None,
           "paid_till": None, "created": None, "free_date": None,
           "registrar": None, "state": None, "nservers": [], "whois_server": None}
    server = _server_for(domain)
    if not server:
        res["error"] = "не удалось определить сервер whois для зоны"
        res["status"] = "unknown"
        return res
    res["whois_server"] = server
    try:
        text = _query(server, domain)
    except OSError as exc:
        res["error"] = "whois недоступен: %s" % str(exc)[:120]
        res["status"] = "unknown"
        return res
    if re.search(r"(?i)no entries found|not found|no match|nothing found", text):
        res["error"] = "домен не найден в реестре"
        res["status"] = "unknown"
        return res

    fields = _collect(text)

    def first(keys):
        for k in keys:
            if k in fields:
                return fields[k][0]
        return None

    raw_expiry = first(EXPIRY_KEYS)
    res["paid_till"] = _parse_date(raw_expiry) if raw_expiry else None
    res["paid_till_raw"] = raw_expiry
    raw_created = first(CREATED_KEYS)
    res["created"] = _parse_date(raw_created) if raw_created else None
    res["registrar"] = first(REGISTRAR_KEYS)
    res["state"] = first(STATE_KEYS)
    res["nservers"] = [ns.rstrip(".").lower() for ns in fields.get("nserver", [])][:6] \
        or [ns.rstrip(".").lower() for ns in fields.get("name server", [])][:6]
    free = first(("free-date",))
    res["free_date"] = _parse_date(free) if free else None

    if res["paid_till"]:
        days = int((res["paid_till"] - time.time()) // 86400)
        res["days_left"] = days
        res["status"] = ("expired" if days < 0 else "critical" if days < crit
                         else "warning" if days < warn else "ok")
    else:
        res["days_left"] = None
        res["status"] = "unknown"
        if not res["error"]:
            res["error"] = "в ответе реестра нет даты окончания"
    return res
