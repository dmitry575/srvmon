"""Is a self-declared search engine really one?

A User-Agent string is whatever the client chose to send, and scrapers
routinely claim to be Googlebot to slip past limits. The check the search
engines themselves document is forward-confirmed reverse DNS: the address
must resolve to a host inside the engine's own domain, and that host must
resolve back to the same address. Either half alone can be faked — a PTR
record is set by whoever owns the address block — together they cannot.

Results are cached per address in SQLite: a verdict holds for a week, a
failed lookup is retried after an hour. Lookups run in a small thread pool
with a per-batch budget, so a log full of new addresses cannot stall the
collector; anything beyond the budget stays "unchecked" until next time.
"""
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import store

# Families that publish rDNS verification, and the domains their crawlers
# resolve into. A family not listed here cannot be confirmed this way.
FAMILIES = [
    ("google", ("googlebot", "google-inspectiontool", "googleother", "google-extended",
                "adsbot-google", "mediapartners-google", "storebot-google"),
     (".googlebot.com", ".google.com", ".googleusercontent.com")),
    ("yandex", ("yandex",), (".yandex.ru", ".yandex.net", ".yandex.com")),
    ("bing", ("bingbot", "msnbot", "bingpreview", "adidxbot"), (".search.msn.com",)),
    ("apple", ("applebot",), (".applebot.apple.com",)),
    ("baidu", ("baiduspider",), (".baidu.com", ".baidu.jp")),
    ("petal", ("petalbot",), (".petalsearch.com",)),
    ("mailru", ("mail.ru_bot",), (".mail.ru",)),
]

VERDICT_TTL = 7 * 86400
FAIL_TTL = 3600
LOOKUP_TIMEOUT = 4.0
BUDGET = 120          # new addresses resolved per batch
WORKERS = 8

SCHEMA = """CREATE TABLE IF NOT EXISTS bot_ip (
  ip TEXT PRIMARY KEY, family TEXT, verdict TEXT, host TEXT, checked INTEGER)"""


def family_of(ua):
    low = (ua or "").lower()
    for name, needles, _ in FAMILIES:
        if any(n in low for n in needles):
            return name
    return None


def _suffixes(family):
    for name, _, sfx in FAMILIES:
        if name == family:
            return sfx
    return ()


def _resolve(ip, family):
    """Forward-confirmed reverse DNS. Returns (verdict, host)."""
    try:
        host = socket.gethostbyaddr(ip)[0].rstrip(".").lower()
    except (socket.herror, socket.gaierror):
        return "fake", ""          # no PTR at all: real crawlers always have one
    except OSError:
        return "unknown", ""
    if not any(("." + host).endswith(s) for s in _suffixes(family)):
        return "fake", host
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "fake", host
    except OSError:
        return "unknown", host
    want = ipaddress.ip_address(ip)
    for info in infos:
        try:
            if ipaddress.ip_address(info[4][0].split("%")[0]) == want:
                return "verified", host
        except ValueError:
            continue
    return "fake", host


class Verifier:
    def __init__(self):
        store.conn().execute(SCHEMA)
        self.cache = {}

    def _cached(self, ip):
        if ip in self.cache:
            return self.cache[ip]
        row = store.one("SELECT verdict, checked FROM bot_ip WHERE ip=?", (ip,))
        if not row:
            return None
        ttl = FAIL_TTL if row["verdict"] == "unknown" else VERDICT_TTL
        if time.time() - row["checked"] > ttl:
            return None
        self.cache[ip] = row["verdict"]
        return row["verdict"]

    def prepare(self, pairs):
        """Resolve, within the budget, the addresses not yet known.

        pairs is an iterable of (ip, family). Lookups run in parallel with a
        hard wall-clock limit: a resolver that hangs costs seconds, not the
        collection cycle."""
        todo = []
        seen = set()
        for ip, family in pairs:
            if ip in seen or self._cached(ip) is not None:
                continue
            seen.add(ip)
            todo.append((ip, family))
            if len(todo) >= BUDGET:
                break
        if not todo:
            return
        pool = ThreadPoolExecutor(max_workers=WORKERS)
        futures = {pool.submit(_resolve, ip, fam): (ip, fam) for ip, fam in todo}
        done, _ = wait(futures, timeout=LOOKUP_TIMEOUT * 3)
        rows = []
        now = int(time.time())
        for fut, (ip, fam) in futures.items():
            verdict, host = fut.result() if fut in done else ("unknown", "")
            self.cache[ip] = verdict
            rows.append((ip, fam, verdict, host, now))
        pool.shutdown(wait=False, cancel_futures=True)
        store.writemany(
            "INSERT OR REPLACE INTO bot_ip(ip,family,verdict,host,checked) VALUES(?,?,?,?,?)",
            rows)

    def verdict(self, ip):
        return self._cached(ip) or "unknown"
