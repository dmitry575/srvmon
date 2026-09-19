<div align="center">

# srvmon

**See what your server is doing — on the cheapest VPS you own.**

One page: which sites are up, what is eating the disk, which database keeps
growing, which certificate expires next week, which service died at 3am.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![No dependencies](https://img.shields.io/badge/dependencies-none-success.svg)](#why-it-is-small)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#requirements)
[![RAM](https://img.shields.io/badge/RAM-~60_MB-success.svg)](#why-it-is-small)

[Русская версия →](README.ru.md)

</div>

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/dmitry575/srvmon/main/install.sh | sudo bash
```

That is it. The installer fetches the latest release, generates a certificate
and a password, sets up two systemd units, and prints the address:

```
══ Done ══

  Open:      https://203.0.113.10:8452
  Login:     admin
  Password:  Kf3nQ8xTr2mWpL9dVe
```

Open the address, sign in, done — nothing else to type, no reverse proxy to
configure first, no DNS record needed. The certificate is self-signed, so the
browser asks once whether you trust it.

<div align="center">

![Overview](docs/screenshots/overview.png)

</div>

---

## Why this exists

A €4/month VPS with 2 cores and 4 GB of RAM running three or four of your own
sites. You want to know what is happening on it. Your options today:

| | RAM it takes | Setup | Fits one small box? |
|---|---|---|---|
| **Grafana + Prometheus + exporters** | 400 MB – 1 GB | hours, three services, a query language | built for fleets |
| **Zabbix** | 1 GB+, needs its own database | a day, an agent, a server, a frontend | built for fleets |
| **Netdata** | 150–300 MB | one command | great at metrics, not at *your sites* |
| **Hosting panels** (cPanel, ISPmanager) | heavy, paid, opinionated | they want to own the machine | they replace your setup |
| **srvmon** | **~60 MB, two processes** | **one command** | **that is the whole point** |

srvmon does not try to be a monitoring platform. It answers the handful of
questions you actually ask about a small server, and gets out of the way.

### Why it is small

* **No dependencies.** Python standard library only — no pip, no virtualenv,
  no Node build step. The frontend is three files served as they are.
* **Two processes.** A web part and a collector, about 30 MB each.
* **SQLite for history**, a few megabytes, pruned automatically.
* Written on a 2-core box with 3.8 GB of RAM, next to the production sites it
  was watching. If it had been heavy, it would have been in the way.

---

## What you get

| Section | What it answers |
|---|---|
| **Overview** | Is the server alive? CPU, memory, swap, disk, load, network, uptime — plus every site, database and alert at a glance |
| **Domains** | Which sites are up, how fast, how many requests, how many errors, when the certificate **and the domain registration** expire |
| **Per-site page** | Uptime over 24h/7d/30d, traffic charts, status codes, the backend process and its unit, recent errors |
| **Databases** | MySQL, PostgreSQL and SQLite side by side: size, tables, rows, indexes, connections, weekly growth |
| **Storage** | Where the disk went — by filesystem, by directory, with a read-only browser |
| **Services** | systemd units: state, memory, ports, uptime, restart count, journal tail |
| **Processes** | What is eating CPU and RAM, grouped by runtime, secrets masked |
| **Network** | Every listening port, which process owns it, public or localhost |
| **Certificates** | TLS expiry and domain registration expiry in one table |
| **Events** | What broke, when, and when it came back |

Interface in **English and Russian**, switchable in the sidebar.

<div align="center">

| | |
|---|---|
| [![Domains](docs/screenshots/domains.png)](docs/screenshots/domains.png)<br>Domains | [![Site](docs/screenshots/domain.png)](docs/screenshots/domain.png)<br>One site in detail |
| [![Databases](docs/screenshots/databases.png)](docs/screenshots/databases.png)<br>Databases | [![PostgreSQL](docs/screenshots/database-postgres.png)](docs/screenshots/database-postgres.png)<br>PostgreSQL internals |
| [![Storage](docs/screenshots/storage.png)](docs/screenshots/storage.png)<br>Storage | [![Certificates](docs/screenshots/certificates.png)](docs/screenshots/certificates.png)<br>Certificates and domains |
| [![Events](docs/screenshots/events.png)](docs/screenshots/events.png)<br>Events | [![Settings](docs/screenshots/settings.png)](docs/screenshots/settings.png)<br>Settings |
| [![Mobile](docs/screenshots/mobile.png)](docs/screenshots/mobile.png)<br>On a phone | |

</div>

---

## Add your sites

Nothing is monitored automatically — a box with a dozen vhosts would produce a
useless list. You add what matters, in **Settings**, and the collector picks it
up without a restart.

To see what the server already runs:

```bash
python3 /opt/srvmon/tools/discover.py
```

It lists nginx server names, application services, listening ports, MySQL and
PostgreSQL databases and SQLite files — as hints for the form, not as facts to
act on.

For each site you fill in: domain, project directory, nginx access and error
logs, certificate path, systemd unit, backend port, database. All optional
except the domain.

---

## Requirements

* Linux with systemd
* Python 3.8+ (no packages needed)
* `openssl` — only to generate the self-signed certificate
* nginx — optional, to read per-site traffic from its logs
* MySQL/MariaDB, PostgreSQL, SQLite — optional, any combination

Tested on Ubuntu 24.04. Anything with systemd and a modern Python should work.

---

## Installation options

```bash
# the usual way
curl -fsSL https://raw.githubusercontent.com/dmitry575/srvmon/main/install.sh | sudo bash

# a different port, a different login
curl -fsSL .../install.sh | sudo bash -s -- --port 9000 --user bob

# keep it private: listen on 127.0.0.1 only, reach it over an SSH tunnel
curl -fsSL .../install.sh | sudo bash -s -- --local
#   ssh -L 8452:127.0.0.1:8452 you@server   → http://127.0.0.1:8452

# from a clone, if you would rather read the code first
git clone https://github.com/dmitry575/srvmon.git /opt/srvmon
cd /opt/srvmon && sudo ./install.sh

# remove the units, keep the collected data
sudo /opt/srvmon/install.sh --uninstall
```

Behind a real domain instead of an IP: `nginx.example.conf` is a ready block
with Let's Encrypt and rate limiting on the login. Then run with `--local` so
the dashboard itself stays on localhost.

### Commands

```bash
systemctl status srvmon srvmon-collector
systemctl restart srvmon srvmon-collector
journalctl -u srvmon-collector -f
python3 /opt/srvmon/tools/passwd.py admin     # change the password
```

---

## Security

The dashboard sees a lot, so it is deliberately unable to do much.

* **Read-only by design.** No shell, no arbitrary SQL, no file writes, no
  deletion, no `systemctl` from the web. It can tell you a service died; it
  cannot restart it.
* **Passwords** are stored as PBKDF2-HMAC-SHA256 hashes, 240 000 iterations.
  The session is a random token in an HttpOnly, SameSite=Strict cookie.
* **It refuses to listen on a public address without TLS** — a password in the
  clear is not a trade-off worth offering.
* **Database passwords are never stored.** MySQL is read through the system
  client and `/etc/mysql/debian.cnf`; PostgreSQL through `psql` running as the
  `postgres` user.
* **The directory browser** is limited to an allowlist, resolves symlinks
  before checking, never reads file contents, and flags known secret filenames.
* **Process command lines are masked** — anything after `password`, `token`,
  `secret` or `api_key` becomes `***`.
* **Demo mode** substitutes hostnames, addresses and paths in API answers, so
  you can publish a screenshot of your own dashboard without publishing your
  infrastructure. Every screenshot in this README was taken that way.

Found something? See [SECURITY.md](SECURITY.md).

---

## What this is not

Being clear about this saves everyone time:

* **Not a hosting panel.** It does not create sites, users, mailboxes or
  databases, and it will not touch your nginx config. It watches; you decide.
* **Not a monitoring platform.** One server, your sites. No agents, no cluster,
  no alerting pipeline — though alerts are there and a notifier is a function
  away.
* **Not a log viewer.** It reads the tail of error logs, not gigabytes of
  history.

If you need any of those, the tools above do them well. srvmon is for the case
where they are more than the machine deserves.

---

## How the numbers are obtained

Every value comes off the machine. Where something cannot be measured with the
current setup, the dashboard says so instead of guessing.

* **System** — `/proc` and `statvfs`, read directly.
* **Site traffic** — nginx access logs, reading only what was appended since
  last time, surviving rotation.
* **Response time** — the dashboard's own HTTPS checks, *not* the logs: the
  combined log format has no timing field, so there is nothing honest to read
  there. If your format does carry it, the parser picks up `rt=`/`urt=`.
* **Databases** — `information_schema` for MySQL; `pg_stat_*` and
  `pg_*_size()` for PostgreSQL; `PRAGMA` and `dbstat` for SQLite, opened
  read-only and immutable so a live writer is never disturbed.
* **Certificates** — a live TLS handshake plus the file on disk, so a renewed
  but not reloaded certificate shows up immediately.
* **Domain expiry** — a direct WHOIS query every 12 hours.

Collection intervals run from 20 seconds for system metrics to 12 hours for
WHOIS, all adjustable in Settings. High-resolution history is kept 7 days,
hourly aggregates 180 days.

---

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Two test suites guard the project and both run on the machine itself:

```bash
SRVMON_PASSWORD='...' python3 tools/selftest.py    # the whole API, against real data
SRVMON_PASSWORD='...' node tools/uitest.js         # every page, in both languages
```

## License

MIT — see [LICENSE](LICENSE).
