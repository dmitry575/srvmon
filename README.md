# srvmon

A self-hosted dashboard for a server that runs several sites of your own.

One page answers the questions you actually ask: is the server alive, which
sites are up, which one is loading the machine, are there errors, is disk space
running out, is a database growing, is a certificate about to expire, did a
service die.

**[Русская версия документации →](README.ru.md)**

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
| **Databases** | Every MySQL and SQLite database: size, tables, rows, indexes, connections, weekly growth |
| **Database page** | Size breakdown, top 20 tables, active connections, size history |
| **Storage** | Filesystems, largest directories with weekly delta, read-only directory browser |
| **Services** | systemd units: state, enabled, PID, memory, ports, uptime, restart count, journal tail |
| **Processes** | Top processes by CPU and memory, grouped by runtime, with secrets masked |
| **Network** | Listening sockets, which process owns them, public vs localhost |
| **Certificates** | Issuer, validity, days left, plus a separate table of domain registration expiry |
| **Events** | Active alerts and a timeline of what broke and recovered |
| **Settings** | Thresholds, collection intervals, and the domain list — editable from the UI |

Interface language: **English and Russian**, switchable in the sidebar.

## Requirements

* Linux with systemd
* Python 3.8 or newer (no packages needed)
* nginx — optional, to expose the dashboard and to parse per-site access logs
* MySQL/MariaDB and/or SQLite — optional, for the database section

PostgreSQL is not supported yet: the tool was built against MySQL and SQLite,
and shipping an untested backend would contradict the "no invented numbers"
rule above. The database layer is a single module (`lib/dbs.py`) if you want to
add it.

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
  `/etc/mysql/debian.cnf`, so no password is stored in the dashboard),
  `PRAGMA` and `dbstat` for SQLite, opened read-only and immutable so a live
  writer is never disturbed.
* **Services** — `systemctl show`.
* **Ports** — `/proc/net/*` with sockets mapped to processes.
* **Certificates** — a live TLS handshake on port 443, plus the file on disk,
  so a renewed-but-not-reloaded certificate is visible immediately.
* **Domain expiry** — a direct WHOIS query on port 43, every 12 hours.

## Collection intervals

| Metric | Interval |
|---|---|
| CPU, memory, disk, network | 20 s |
| Site health checks | 45 s |
| nginx log parsing | 60 s |
| Services, ports, processes | 60 s |
| Databases | 5 min |
| Directory scan (`du`) | 15 min |
| Certificates | 1 h |
| Domain registration (WHOIS) | 12 h |

High-resolution history is kept for 7 days, hourly aggregates for 180 days,
and old rows are pruned automatically. All intervals and thresholds are
editable in Settings.

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

## Contributing

Bug reports and patches are welcome. Before opening a pull request, run both
test suites above; `uitest.js` in particular will fail if a new interface
string has no translation.

## License

MIT — see [LICENSE](LICENSE).
