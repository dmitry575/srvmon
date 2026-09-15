# Changelog

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
