# Changelog

## 1.0.1

Fixes found by adding a third site to a dashboard that was already running.
Nobody had done that before: every earlier domain was there from the first
start, so the paths that handle a late arrival had never been exercised.

### Fixed

* **The first chart point was a lie.** On its first encounter with an access
  log the parser read half a megabyte of tail and counted it as fresh traffic,
  so a whole day of requests landed in a single minute of the chart. It now
  records where the file ends and returns nothing; the past is rebuilt
  separately and spread across its own timestamps. On the site that exposed
  this, one false spike of 2072 requests became 219 truthful points.
* **A site added later never got its history.** Rebuilding from logs happened
  only while the traffic table was empty as a whole, and by then the ongoing
  collection had already written a row. The check is now per domain and looks
  for points older than an hour.
* **A new site looked half empty for hours.** Certificates refresh hourly,
  WHOIS every twelve hours, directory sizes every fifteen minutes — a domain
  added a minute ago had to wait all of that out. The collector now notices
  domains appearing in the configuration and runs those tasks at once. The
  WHOIS freshness check became per domain for the same reason, so one recent
  answer no longer holds back a lookup for a different domain.
* The self-test no longer assumes a fixed number of domains.

## 1.0.0

First public release.

### Sections

Overview, Domains and a page per domain, Databases and a page per database,
Storage with a read-only directory browser, Services, Processes, Network,
Certificates, Events, Settings.

### Data sources

* System metrics from `/proc` and `statvfs`, no psutil.
* Site traffic from nginx access logs, reading only the appended part and
  surviving rotation.
* Response time from the dashboard's own HTTPS checks — the combined log
  format carries no timing field, so nothing is inferred from logs.
* MySQL through the stock client and `/etc/mysql/debian.cnf`.
* PostgreSQL through `psql` running as the `postgres` system user: size split
  into data, indexes and TOAST, dead rows, vacuum and analyze times, seq vs
  index scans, largest indexes, connections by state.
* SQLite opened read-only and immutable, with `dbstat` sizes when available.
* systemd units through `systemctl show`, ports from `/proc/net/*`.
* Certificates from a live TLS handshake plus the file on disk.
* Domain registration expiry from a direct WHOIS query.

### Properties

* Python standard library only; no pip, no virtualenv, no frontend build.
* Two processes, roughly 30 MB of RAM each; metrics in SQLite.
* Read-only: no shell, no arbitrary SQL, no writes, no service control.
* Interface in English and Russian, switchable in the sidebar.
* Install with `./install.sh`; new sites are added through Settings without
  touching the code.
