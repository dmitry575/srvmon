#!/usr/bin/env python3
"""Что есть на этом сервере.

Показывает найденные домены nginx, службы, порты, базы и каталоги, чтобы
конфигурацию не пришлось составлять вслепую. Ничего не меняет: обнаруженный
домен не становится наблюдаемым сам по себе — его нужно добавить осознанно,
через раздел Settings или правку config.json.

    python3 tools/discover.py           показать найденное
    python3 tools/discover.py --json    то же в виде JSON
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import dbs, net, sysinfo  # noqa: E402

NGINX_DIRS = ["/etc/nginx/sites-enabled", "/etc/nginx/conf.d",
              "/etc/nginx/vhosts", "/usr/local/nginx/conf/conf.d"]
SKIP_NAMES = {"_", "localhost", "default_server", "default"}


def nginx_sites():
    """Имена серверов вместе с журналами и сертификатами из тех же блоков."""
    found = {}
    for d in NGINX_DIRS:
        for path in sorted(glob.glob(os.path.join(d, "*"))):
            if not os.path.isfile(path):
                continue
            # Выключенные и отложенные файлы nginx не читает — и мы не будем.
            if path.endswith((".disabled", ".bak", ".save", ".dpkg-dist", "~")):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            names = set()
            for m in re.finditer(r"^\s*server_name\s+([^;]+);", text, re.M):
                for n in m.group(1).split():
                    n = n.strip()
                    if n and n not in SKIP_NAMES and not n.startswith("~") \
                            and not re.fullmatch(r"[\d.:]+", n):
                        names.add(n.lstrip("*."))
            if not names:
                continue
            access = re.search(r"^\s*access_log\s+([^\s;]+)", text, re.M)
            error = re.search(r"^\s*error_log\s+([^\s;]+)", text, re.M)
            cert = re.search(r"^\s*ssl_certificate\s+([^\s;]+)", text, re.M)
            root = re.search(r"^\s*root\s+([^\s;]+)", text, re.M)
            upstream = re.search(r"proxy_pass\s+https?://(?:[\w.-]+:)?(\d+)", text)
            for n in names:
                entry = found.setdefault(n, {"domain": n, "nginx_conf": [],
                                             "access_log": None, "error_log": None,
                                             "ssl_cert": None, "project_dir": None,
                                             "backend_port": None})
                entry["nginx_conf"].append(path)
                entry["access_log"] = entry["access_log"] or (access.group(1) if access else None)
                entry["error_log"] = entry["error_log"] or (error.group(1) if error else None)
                entry["ssl_cert"] = entry["ssl_cert"] or (cert.group(1) if cert else None)
                entry["project_dir"] = entry["project_dir"] or (root.group(1) if root else None)
                if upstream and not entry["backend_port"]:
                    entry["backend_port"] = int(upstream.group(1))
    return sorted(found.values(), key=lambda e: e["domain"])


def app_services():
    """Службы, похожие на прикладные: systemd-собственные отсеиваем."""
    # По умолчанию показываем только работающие: перечень всех юнитов системы
    # состоит в основном из одноразовых задач загрузки и пользы не несёт.
    args = ["systemctl", "list-units", "--type=service", "--no-pager",
            "--no-legend", "--plain"]
    if "--all-services" in sys.argv:
        args.insert(3, "--all")
    code, out, _ = sysinfo.run(args, timeout=20)
    skip = ("systemd-", "user@", "getty", "dbus", "snap.", "polkit", "udisks",
            "upower", "ModemManager", "multipathd", "unattended", "fwupd",
            "rsyslog", "cron", "ssh", "networkd", "resolved", "qemu-", "apparmor",
            "console-setup", "keyboard-setup", "kmod", "plymouth", "setvtrgb",
            "blk-availability", "e2scrub", "finalrd", "logrotate", "man-db",
            "packagekit", "accounts-daemon", "cups", "avahi", "bluetooth",
            "apport", "apt-daily", "cloud-init", "dm-event", "dmesg",
            "dpkg-db-backup", "emergency", "grub", "initrd", "iscsi", "ldconfig",
            "lvm2", "rescue", "modprobe", "netplan", "open-iscsi", "pollinate",
            "rc-local", "secureboot", "ua-", "ubuntu-", "update-notifier",
            "motd-news", "sysstat", "swap", "uuidd")
    res = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        name, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        if not name.endswith(".service") or load != "loaded":
            continue
        if any(name.startswith(s) for s in skip):
            continue
        res.append({"name": name, "active": active, "sub": sub,
                    "description": parts[4] if len(parts) > 4 else ""})
    return res


def databases(cfg):
    out = {"mysql": [], "sqlite_candidates": [], "mysql_error": None}
    defaults = cfg.get("mysql_defaults_file", "/etc/mysql/debian.cnf")
    if os.path.exists(defaults):
        rows, err = dbs.mysql_databases(defaults)
        out["mysql_error"] = err
        out["mysql"] = [{"name": d["name"], "size": d["size"], "tables": d["tables"]}
                        for d in rows if not d.get("system")]
    else:
        out["mysql_error"] = "нет файла %s" % defaults
    seen = set()
    for base in ("/var/www", "/srv", "/opt", "/home", "/root"):
        if not os.path.isdir(base):
            continue
        for depth in ("*/*.sqlite", "*/*/*.sqlite", "*/*/*/*.sqlite",
                      "*/*.db", "*/*/*.db", "*/*/*/*.db"):
            for p in glob.glob(os.path.join(base, depth))[:200]:
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if size < 4096 or p in seen:
                    continue
                seen.add(p)
                out["sqlite_candidates"].append({"path": p, "size": size})
    out["sqlite_candidates"].sort(key=lambda d: -d["size"])
    out["sqlite_candidates"] = out["sqlite_candidates"][:25]
    return out


def collect():
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
        except ValueError:
            cfg = {}
    ports = net.annotate(net.listening(include_udp=False), cfg)
    return {
        "os": sysinfo.os_release(),
        "sites": nginx_sites(),
        "services": app_services(),
        "ports": [p for p in ports if not p.get("ephemeral")],
        "databases": databases(cfg),
        "filesystems": sysinfo.filesystems(),
    }


def human(data):
    ru = not os.environ.get("LANG", "").lower().startswith("en")
    def s(rus, eng):
        return rus if ru else eng

    print(s("Система:", "System:"), data["os"]["name"], "|", data["os"]["cpu_model"],
          "|", data["os"]["cores"], s("ядра", "cores"))
    print()
    print(s("Домены в конфигурации nginx (%d). Поля ниже — подсказка, а не готовая настройка:",
            "Domains in nginx config (%d). The fields below are hints, not final settings:")
          % len(data["sites"]))
    for site in data["sites"]:
        print("  %-28s" % site["domain"])
        for key, label in (("project_dir", s("каталог", "root")),
                           ("access_log", s("журнал", "access log")),
                           ("ssl_cert", s("сертификат", "certificate")),
                           ("backend_port", s("порт бэкенда", "backend port"))):
            if site.get(key):
                print("      %-14s %s" % (label, site[key]))
    print()
    running = [x for x in data["services"] if x["active"] == "active"]
    print(s("Прикладные службы (%d активных из %d показанных):",
            "Application services (%d running of %d shown):")
          % (len(running), len(data["services"])))
    for svc in data["services"][:25]:
        print("  %-30s %-10s %s" % (svc["name"], svc["active"], svc["description"][:48]))
    print()
    print(s("Слушающие порты (%d):", "Listening ports (%d):") % len(data["ports"]))
    for p in data["ports"][:25]:
        print("  %-7s %-10s %-16s %s" % (p["port"], p["proto"], p["process"], p["scope"]))
    print()
    db = data["databases"]
    print(s("Базы MySQL:", "MySQL databases:"))
    if db["mysql"]:
        for d in db["mysql"]:
            print("  %-24s %10.1f МБ  %s %s" % (d["name"], d["size"] / 1048576,
                                                d["tables"], s("таблиц", "tables")))
    else:
        print("  —", db["mysql_error"] or s("не найдено", "none found"))
    print()
    print(s("Найденные файлы SQLite:", "SQLite files found:"))
    for d in db["sqlite_candidates"][:12]:
        print("  %-52s %8.1f МБ" % (d["path"], d["size"] / 1048576))
    print()
    print(s("Добавьте нужное в разделе Settings — само ничего не включится.",
            "Add what you need in Settings — nothing is monitored automatically."))


def main():
    data = collect()
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        human(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
