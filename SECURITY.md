# Security

## Reporting a vulnerability

Open a [security advisory](https://github.com/dmitry575/srvmon/security/advisories/new)
rather than a public issue. If that is not available to you, open an issue
saying only that you have found something and how to reach you.

## What the dashboard can and cannot do

Worth knowing before you audit it. The dashboard is read-only by construction:

* no route executes a shell command;
* no route accepts SQL — every statement is hard-coded;
* nothing on the server is written, changed or deleted, except the
  dashboard's own `config.json` through the Settings page;
* services cannot be started, stopped or restarted from the web;
* the directory browser is limited to an allowlist, resolves symlinks before
  checking each path, and never reads file contents.

## Credentials

* The dashboard password is stored only as a PBKDF2-HMAC-SHA256 hash
  (240 000 iterations, per-user salt) in `var/users.json`, mode 600.
* Sessions are random tokens in an HttpOnly, SameSite=Strict cookie.
* Database passwords are never stored: MySQL is read through the system client
  and `/etc/mysql/debian.cnf`; PostgreSQL through `psql` running as the
  `postgres` system user, or a DSN whose password lives in `~/.pgpass`.
* The server refuses to listen on a public address without TLS.

## Known exposure

* The default certificate is **self-signed**. Traffic is encrypted, but the
  certificate vouches for itself, so the first connection cannot be
  distinguished from an interception. For a real domain, put it behind nginx
  with Let's Encrypt (`nginx.example.conf`) and run with `--local`.
* Anyone who can sign in sees a great deal about the server: processes, ports,
  database sizes, directory listings. Treat the password accordingly.
* Brute-force protection lives in the nginx example config, not in the
  application. Behind the built-in server, use a long password.
