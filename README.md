# srvmon

A self-hosted dashboard for a server that runs several sites of your own.

One page answers the questions you actually ask: is the server alive, which
sites are up, which one is loading the machine, are there errors, is disk space
running out, is a database growing, is a certificate about to expire, did a
service die.

**[Русская версия документации →](README.ru.md)**

![Overview](docs/screenshots/overview.png)

<sub>Screenshots are taken from a live install with demo mode on: real numbers
and real charts, with hostnames, addresses and paths substituted. See
[Demo mode](#demo-mode).</sub>

---

## Why another dashboard

Prometheus, Grafana and Zabbix are built for fleets. For one box with three or
four sites on it, the monitoring stack ends up larger than what it monitors.
srvmon takes the opposite position:

* **No dependencies.** Python standard library only — no pip install, no
  virtualenv, no Node build. Three files make up the frontend.
* **Cheap.** Two Python processes, roughly 30 MB of RAM each, and an SQLite
  file that stays in the low megabytes. It was written on a 2-core box with
  3.8 GB of RAM, next to two production sites.
* **Read-only by design.** No shell, no arbitrary SQL, no file writes, no
  `systemctl` from the web. The dashboard can tell you a service died; it
  cannot restart it.
* **No invented numbers.** Every value comes from the machine. Where something
  cannot be measured with the current setup, the dashboard says so instead of
  guessing.

## What it shows

| Section | Contents |
|---|---|
| **Overview** | CPU, memory, swap, disk, load average, network, uptime, temperature; site summary; database summary; where disk space went; active alerts |
| **Domains** | Status, HTTP/HTTPS codes, response time, requests and requests/min, 4xx and 5xx, uptime, certificate and domain expiry, backend, port, project size |
| **Domain page** | Availability over 24 h / 7 d / 30 d, traffic by period, charts of requests, response time and status codes, backend process and unit, certificate, domain registration, recent errors |
| **Databases** | Every MySQL, PostgreSQL and SQLite database: size, tables, rows, indexes, connections, weekly growth |
| **Database page** | Size breakdown (data, indexes, TOAST), top 20 tables, largest indexes, active connections, size history |
| **Storage** | Filesystems, largest directories with weekly delta, read-only directory browser |
| **Services** | systemd units: state, enabled, PID, memory, ports, uptime, restart count, journal tail |
| **Processes** | Top processes by CPU and memory, grouped by runtime, with secrets masked |
| **Network** | Listening sockets, which process owns them, public vs localhost |
| **Certificates** | Issuer, validity, days left, plus a separate table of domain registration expiry |
| **Events** | Active alerts and a timeline of what broke and recovered |
| **Settings** | Thresholds, collection intervals, and the domain list — editable from the UI |

Interface language: **English and Russian**, switchable in the sidebar.


## Screenshots

| | |
|---|---|
| [![Domains](docs/screenshots/domains.png)](docs/screenshots/domains.png)<br>**Domains** — status, response time, requests, errors, certificate and registration expiry | [![Domain](docs/screenshots/domain.png)](docs/screenshots/domain.png)<br>**One domain** — availability, traffic, charts, backend, certificate, registration |
| [![Databases](docs/screenshots/databases.png)](docs/screenshots/databases.png)<br>**Databases** — MySQL, PostgreSQL and SQLite side by side | [![PostgreSQL](docs/screenshots/database-postgres.png)](docs/screenshots/database-postgres.png)<br>**A PostgreSQL database** — data, indexes and TOAST, dead rows, vacuum, largest indexes |
| [![Storage](docs/screenshots/storage.png)](docs/screenshots/storage.png)<br>**Storage** — filesystems, largest directories, read-only browser | [![Certificates](docs/screenshots/certificates.png)](docs/screenshots/certificates.png)<br>**Certificates** — expiry of TLS certificates and of the domains themselves |
| [![Events](docs/screenshots/events.png)](docs/screenshots/events.png)<br>**Events** — what is wrong now and a timeline of what broke and recovered | [![Settings](docs/screenshots/settings.png)](docs/screenshots/settings.png)<br>**Settings** — thresholds, intervals, and the domain list |
| [![Mobile](docs/screenshots/mobile.png)](docs/screenshots/mobile.png)<br>**On a phone** — the same data, one column | |

The Processes, Ports and Services pages are intentionally not shown: even with
demo mode on they display real command lines and port numbers of everything
else running on the machine. Names can be substituted; numbers cannot, since
rewriting digits in an answer would also corrupt sizes and timestamps.

## Requirements

* Linux with systemd
* Python 3.8 or newer (no packages needed)
* nginx — optional, to expose the dashboard and to parse per-site access logs
* MySQL/MariaDB, PostgreSQL and/or SQLite — optional, for the database section

All three engines can be present at once; the list shows them side by side.
Any of them can be absent — the section simply skips what is not there, and a
database server that is installed but down is reported as an error rather than
silently omitted.

## Install

```bash
git clone https://github.com/YOURNAME/srvmon.git /opt/srvmon
cd /opt/srvmon
sudo ./install.sh
```

That is the whole installation. The script checks the environment, writes
`config.json`, creates a user with a generated password, installs two systemd
units, starts them, and prints the address and credentials.

Useful flags:

```bash
sudo ./install.sh --port 9000        # different local port
sudo ./install.sh --user bob         # different login
sudo ./install.sh --no-start         # prepare only
sudo ./install.sh --uninstall        # remove units, keep data
```

Then see what the server runs, and add what you want to watch:

```bash
python3 tools/discover.py
```

It lists nginx server names, application services, listening ports, MySQL
databases and SQLite files it found — as hints. **Nothing becomes monitored on
its own.** You add domains explicitly in Settings, which is what keeps the list
meaningful on a box with a dozen vhosts.

## Exposing it

The dashboard binds to `127.0.0.1` and never listens on a public interface.
Pick one:

* **A hostname** — copy `nginx.example.conf`, adjust names and certificate
  paths, reload nginx. Preferred.
* **SSH tunnel** — nothing to configure at all:
  `ssh -L 8452:127.0.0.1:8452 you@server`, then open `http://127.0.0.1:8452`.
* **Server IP on a separate port** — works if you have a certificate issued for
  the IP address itself. Let's Encrypt issues those.

## How the numbers are obtained

* **System** — `/proc` and `statvfs`, read directly.
* **Site traffic** — nginx access logs, reading only what was appended since
  last time and surviving rotation. Requests, status distribution, bytes.
* **Response time** — the dashboard's own HTTPS checks, *not* the logs. The
  common `combined` log format has no `$request_time` field, so there is
  nothing honest to read there. If your format does include timing, the parser
  picks up `rt=` and `urt=` automatically.
* **Databases** — `information_schema` for MySQL (via the system client and
  `/etc/mysql/debian.cnf`, so no password is stored in the dashboard);
  `pg_stat_*` and `pg_*_size()` for PostgreSQL (via `psql` running as the
  `postgres` system user — peer authentication, again no stored password);
  `PRAGMA` and `dbstat` for SQLite, opened read-only and immutable so a live
  writer is never disturbed. Row counts are planner estimates on both server
  engines: an exact count would mean a full scan on every page load.
* **Services** — `systemctl show`.
* **Ports** — `/proc/net/*` with sockets mapped to processes.
* **Certificates** — a live TLS handshake on port 443, plus the file on disk,
  so a renewed-but-not-reloaded certificate is visible immediately.
* **Domain expiry** — a direct WHOIS query on port 43, every 12 hours.

### PostgreSQL specifics

The database page shows what only PostgreSQL can tell you: size split into
data, indexes and TOAST; dead rows per table; when each table was last
vacuumed and analyzed; sequential versus index scans; the largest indexes with
their usage count (an index with zero scans is a candidate for removal); and
connections by state, including ones stuck idle in transaction.

Connection settings live under `postgres` in `config.json`:

```json
"postgres": {
  "enabled": true,
  "run_as": "postgres",
  "dsn": ""
}
```

`run_as` is the system user `psql` is launched as — the standard Debian/Ubuntu
peer authentication, which works when the dashboard runs as root. For anything
else, put a connection string in `dsn`; the password then comes from the
`~/.pgpass` of the user running the dashboard and is never stored here. Setting
`enabled` to false skips PostgreSQL entirely.

Alerts cover connection count against `max_connections` and connections stuck
in `idle in transaction`, with thresholds in Settings.

## Collection intervals

| Metric | Interval |
|---|---|
| CPU, memory, disk, network | 20 s |
| Site health checks | 45 s |
| nginx log parsing | 60 s |
| Services, ports, processes | 60 s |
| Databases (all engines) | 5 min |
| Directory scan (`du`) | 15 min |
| Certificates | 1 h |
| Domain registration (WHOIS) | 12 h |

High-resolution history is kept for 7 days, hourly aggregates for 180 days,
and old rows are pruned automatically. All intervals and thresholds are
editable in Settings.


## Demo mode

Publishing a screenshot of your own dashboard means publishing your hostnames,
addresses and paths. Demo mode substitutes them in the API answers, so the
picture stays truthful in every number while giving nothing away:

```json
"redact": {
  "enabled": true,
  "replace": {
    "203.0.113.10": "1.1.1.1",
    "my-real-site.com": "example.com",
    "/srv/my-project": "/srv/example"
  }
}
```

Collected data is untouched — only what leaves through the API is rewritten,
and links keep working because identifiers are translated back on the way in.
Two notes from building it: a purely numeric rule is ignored, because replacing
digits in a serialised answer also rewrites sizes and timestamps; and a
replacement equal to some other original is dropped, because it would be
translated back and break the page's own links.

The screenshots in this README were produced with:

```bash
SRVMON_PASSWORD='...' node tools/screenshots.js --out docs/screenshots
```

It drives a headless Chromium over the DevTools protocol — no puppeteer, no
node_modules.

## Security

* Login with a password hashed using PBKDF2-HMAC-SHA256, 240 000 iterations.
* Session is a random token; the cookie is HttpOnly and SameSite=Strict.
* Brute force is rate-limited in nginx, before the request reaches Python.
* No command execution, no arbitrary SQL, no writes, no deletion, no service
  control.
* The directory browser is restricted to an allowlist (`browse_roots` in the
  configuration), resolves symlinks before checking, never reads file contents,
  and flags known secret filenames — extend the list with `sensitive_names`.
* Process command lines are masked: anything after `password`, `token`,
  `secret` or `api_key` becomes `***`.
* `noindex` is set both in the page and as a header.

## Layout

```
server.py          web server and API, binds to 127.0.0.1 only
collector.py       metrics collection, a separate process
config.json        what to watch (created by install.sh, git-ignored)
lib/               collection modules: system, logs, databases, services,
                   ports, TLS, WHOIS, storage, alerts
web/               interface: index.html, app.js, style.css, i18n.js
tools/passwd.py    create or change a user
tools/discover.py  show what this server runs
tools/selftest.py  exercise the whole API and check the values
tools/uitest.js    render every page in both languages, catch missing strings
var/               metrics database and users file (git-ignored)
```

The collector runs as its own unit on purpose: restarting the web part never
interrupts history.

## Commands

```bash
systemctl status srvmon srvmon-collector
systemctl restart srvmon srvmon-collector
journalctl -u srvmon-collector -f

SRVMON_PASSWORD='...' python3 tools/selftest.py    # API and values
SRVMON_PASSWORD='...' node tools/uitest.js         # pages, both languages
```

`tools/uitest.js` needs Node only to run the checks; the dashboard itself does
not use Node at all.

## Alerts

Alerts are evaluated against thresholds and stored as events that open and
close themselves, so the Events page shows a real timeline rather than a wall
of repeats.

Delivery to Telegram, email or Discord is not implemented. The hook is ready:
register a function with `alerts.NOTIFIERS` in `lib/alerts.py` and it receives
the active alert list. Alert texts carry a template id and parameters, so a
notifier can render them in any language.

## Adding a site later

Settings → fill in domain, project directory, nginx logs, certificate path,
systemd unit, backend port, database → Save. The collector picks up the change
without a restart. No code changes, which was the point.

## Releases

A release is built by CI from a tag. Pushing `v1.2.3` runs the same checks the
main branch gets, verifies that the tag matches `VERSION` in `lib/__init__.py`,
packs an archive without local state, takes the notes for that version out of
`CHANGELOG.md`, and publishes them together with a SHA-256 sum.

```bash
# bump VERSION in lib/__init__.py, add a section to CHANGELOG.md, then:
git tag -a v1.2.3 -m "srvmon 1.2.3"
git push origin v1.2.3
```

## Contributing

Bug reports and patches are welcome. Before opening a pull request, run both
test suites above; `uitest.js` in particular will fail if a new interface
string has no translation.

## License

MIT — see [LICENSE](LICENSE).
