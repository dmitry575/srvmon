"""What an error response actually was.

A status code alone does not say who answered or why. On one site a 403 can
come from nginx refusing a request outright and, two lines later, from the
application's own rate guard; a 404 can be a broken link or a vulnerability
scanner probing for /wp-admin. Lumped together they make a protection doing
its job look like an outage, or hide a real outage among blocked bots.

Each error line is put into a category and a client class. Generic rules cover
what holds for any site; a site can add its own rules in config.json
("error_rules") for knowledge only its owner has — which limits exist and what
their responses look like. Site rules are checked first.

The combined log format records neither who produced the response nor the
upstream status, so the generic split between "nginx answered" and "the
application answered" rests on body size: nginx's own error pages are a few
hundred bytes, an application page is rarely that small. This is a heuristic
and is labelled as one on the page; a site rule replaces it with certainty.
"""
import re

from .botverify import family_of as botverify_family

# kind groups categories for the page: protection is the site defending
# itself, server is a fault, client is the visitor's side, missing is content.
CATEGORIES = {
    "protect-nginx": "protection",
    "protect-app": "protection",
    "server": "server",
    "backend": "server",
    "client-gone": "client",
    "bad-request": "client",
    "auth": "client",
    "scanner": "missing",
    "missing": "missing",
    "gone": "missing",
    "other": "client",
}

SCANNER_RE = re.compile(
    r"(?i)(/wp-(admin|login|includes|content)|xmlrpc\.php|/\.env|/\.git|/\.aws|"
    r"phpmyadmin|/cgi-bin/|\.(php|asp|aspx|jsp|cgi)(\?|$)|/vendor/phpunit|"
    r"/actuator|/boaform|/HNAP1|/owa/|/autodiscover)")

SEARCH_RE = re.compile(r"(?i)yandex|googlebot|google-inspectiontool|bingbot|"
                       r"applebot|duckduckbot|mail\.ru_bot|petalbot")
BOT_RE = re.compile(r"(?i)bot|crawl|spider|slurp|scan|fetch|archiver|monitor")
TOOL_RE = re.compile(r"(?i)^(curl|wget|python|go-http|java|okhttp|scrapy|"
                     r"httpclient|libwww|axios|node-fetch|aiohttp|php|ruby)")

NGINX_PAGE_MAX = 300   # nginx's built-in error pages fit well under this


def client_class(ua):
    ua = (ua or "").strip()
    if not ua or ua == "-":
        return "empty"
    if SEARCH_RE.search(ua):
        return "search"
    if TOOL_RE.search(ua):
        return "tool"
    if BOT_RE.search(ua):
        return "bot"
    return "browser"


def _compile(rules):
    out = []
    for r in rules or []:
        try:
            out.append({
                "status": set(int(s) for s in r.get("status", [])),
                "min_bytes": r.get("min_bytes"),
                "max_bytes": r.get("max_bytes"),
                "path": re.compile(r["path_re"]) if r.get("path_re") else None,
                "category": r.get("category") if r.get("category") in CATEGORIES else "other",
                "rule": str(r.get("id") or r.get("label_en") or r.get("label") or "rule")[:40],
            })
        except (re.error, TypeError, ValueError):
            continue
    return out


class Classifier:
    def __init__(self, site_rules=None):
        self.rules = _compile(site_rules)

    def classify(self, status, nbytes, target, ua):
        """Returns (category, rule id or "", client class)."""
        client = client_class(ua)
        for r in self.rules:
            if r["status"] and status not in r["status"]:
                continue
            if r["min_bytes"] is not None and nbytes < r["min_bytes"]:
                continue
            if r["max_bytes"] is not None and nbytes > r["max_bytes"]:
                continue
            if r["path"] and not r["path"].search(target):
                continue
            return r["category"], r["rule"], client
        return self._generic(status, nbytes, target), "", client

    @staticmethod
    def _generic(status, nbytes, target):
        if status == 499:
            return "client-gone"
        if status in (502, 504):
            return "backend"
        if status >= 500:
            return "server"
        if status in (403, 429, 444):
            return "protect-nginx" if nbytes <= NGINX_PAGE_MAX else "protect-app"
        if status in (404, 410, 405) and SCANNER_RE.search(target):
            return "scanner"
        if status == 404:
            return "missing"
        if status == 410:
            return "gone"
        if status == 401:
            return "auth"
        if status in (400, 405, 408, 411, 413, 414, 431):
            return "bad-request"
        return "other"


def shape_path(target):
    """The path with query values dropped: /dataset/x?row=&lang= .

    Values are what makes every request unique; parameter names are what
    tells one kind of request from another."""
    path, _, query = target.partition("?")
    if query:
        names = []
        for part in query.split("&"):
            name = part.split("=", 1)[0]
            if name and name not in names:
                names.append(name)
        path += "?" + "&".join(n + "=" for n in names[:4])
    return path[:120]


LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?:\S+ )?(?P<target>\S*)[^"]*" '
    r'(?P<status>\d{3}) (?P<bytes>\d+|-) "[^"]*" "(?P<ua>[^"]*)"')


def tally(lines, classifier, parse_time=None, bucket=None, verifier=None):
    """Count error lines by (status, category, rule, client).

    With bucket set, counts are also split by time bucket — used when history
    is rebuilt from a log, so a day of errors is spread over its own hours
    instead of landing in the minute the rebuild happened."""
    counts = {}
    paths = {}
    matched = []
    claims = []
    for line in lines:
        m = LINE_RE.match(line)
        if not m or m.group("status")[0] not in "45":
            continue
        matched.append(m)
        fam = botverify_family(m.group("ua"))
        if fam:
            claims.append((m.group("ip"), fam))
    # A User-Agent is only a claim. Addresses that say they are a search
    # engine are confirmed by DNS before being counted as one.
    if verifier is not None and claims:
        verifier.prepare(claims)
    for m in matched:
        status = int(m.group("status"))
        nb = m.group("bytes")
        nbytes = int(nb) if nb.isdigit() else 0
        target = m.group("target")
        cat, rule, client = classifier.classify(status, nbytes, target, m.group("ua"))
        if client == "search":
            fam = botverify_family(m.group("ua"))
            if fam is None:
                client = "bot"            # no way to confirm this engine by DNS
            elif verifier is None:
                client = "search-unchecked"
            else:
                v = verifier.verdict(m.group("ip"))
                client = {"verified": "search", "fake": "fake-search"}.get(v, "search-unchecked")
        when = 0
        if bucket and parse_time:
            ts = parse_time(m.group("ts"))
            if ts is None:
                continue
            when = ts - ts % bucket
        key = (when, status, cat, rule, client)
        counts[key] = counts.get(key, 0) + 1
        pkey = (when, status, cat, rule)
        bucket_paths = paths.setdefault(pkey, {})
        shaped = shape_path(target)
        bucket_paths[shaped] = bucket_paths.get(shaped, 0) + 1
    return counts, paths
