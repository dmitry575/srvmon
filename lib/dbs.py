"""Базы данных: MySQL и файловые SQLite.

PostgreSQL на этом сервере нет — работают MySQL 8.0 и набор SQLite-файлов
у обоих сайтов, поэтому раздел построен вокруг них. Пароль MySQL не хранится
в панели: используется штатный /etc/mysql/debian.cnf, который читает только root.
"""
import os
import sqlite3
import time

from .sysinfo import run

MYSQL = "/usr/bin/mysql"


def _mysql(sql, defaults_file, timeout=25):
    """Читающие запросы к MySQL идут через клиент с --defaults-file.
    Панель нигде не принимает SQL снаружи: все запросы зашиты в коде."""
    if not os.path.exists(MYSQL):
        return None, "клиент mysql не найден"
    cmd = [MYSQL, f"--defaults-file={defaults_file}", "--batch", "--raw",
           "--skip-column-names", "-e", sql]
    code, out, err = run(cmd, timeout=timeout)
    if code != 0:
        return None, (err.strip().splitlines() or ["ошибка запроса"])[-1][:200]
    return [line.split("\t") for line in out.splitlines() if line], None


def mysql_available(defaults_file):
    rows, err = _mysql("SELECT 1", defaults_file, timeout=8)
    return rows is not None, err


def mysql_databases(defaults_file):
    sql = ("SELECT table_schema, COUNT(*), COALESCE(SUM(data_length),0), "
           "COALESCE(SUM(index_length),0), COALESCE(SUM(data_free),0), "
           "COALESCE(SUM(table_rows),0) "
           "FROM information_schema.tables GROUP BY table_schema")
    rows, err = _mysql(sql, defaults_file)
    if rows is None:
        return [], err
    system = {"mysql", "information_schema", "performance_schema", "sys"}
    conns = mysql_connections_by_db(defaults_file)
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        name = r[0]
        data, idx = int(r[2]), int(r[3])
        out.append({
            "engine": "mysql", "name": name, "tables": int(r[1]),
            "size": data + idx, "data_size": data, "index_size": idx,
            "free": int(r[4]), "rows": int(r[5]),
            "connections": conns.get(name, 0),
            "system": name in system,
        })
    out.sort(key=lambda d: -d["size"])
    return out, None


def mysql_connections_by_db(defaults_file):
    rows, err = _mysql(
        "SELECT COALESCE(db,'-'), COUNT(*) FROM information_schema.processlist GROUP BY db",
        defaults_file)
    if rows is None:
        return {}
    return {r[0]: int(r[1]) for r in rows if len(r) > 1}


def mysql_status(defaults_file):
    rows, err = _mysql(
        "SHOW GLOBAL STATUS WHERE Variable_name IN "
        "('Threads_connected','Threads_running','Uptime','Queries','Slow_queries',"
        "'Aborted_connects','Innodb_buffer_pool_reads','Connections'); "
        "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('max_connections','version');",
        defaults_file)
    if rows is None:
        return {}, err
    st = {r[0]: r[1] for r in rows if len(r) > 1}
    return st, None


def mysql_tables(defaults_file, dbname, limit=20):
    sql = (
        "SELECT t.table_name, COALESCE(t.table_rows,0), COALESCE(t.data_length,0), "
        "COALESCE(t.index_length,0), COALESCE(t.data_free,0), t.engine, "
        "COALESCE(DATE_FORMAT(t.update_time,'%%Y-%%m-%%d %%H:%%i'),''), "
        "COALESCE(DATE_FORMAT(t.create_time,'%%Y-%%m-%%d %%H:%%i'),'') "
        "FROM information_schema.tables t WHERE t.table_schema='{db}' "
        "ORDER BY (COALESCE(t.data_length,0)+COALESCE(t.index_length,0)) DESC LIMIT {lim}"
    ).replace("%%", "%").format(db=dbname.replace("'", ""), lim=int(limit))
    rows, err = _mysql(sql, defaults_file)
    if rows is None:
        return [], err
    out = []
    for r in rows:
        if len(r) < 8:
            continue
        out.append({"name": r[0], "rows": int(r[1]), "data_size": int(r[2]),
                    "index_size": int(r[3]), "free": int(r[4]),
                    "total_size": int(r[2]) + int(r[3]), "engine": r[5],
                    "updated": r[6], "created": r[7]})
    return out, None


def mysql_processlist(defaults_file, limit=25):
    rows, err = _mysql(
        "SELECT id, user, COALESCE(db,''), command, time, COALESCE(state,'') "
        "FROM information_schema.processlist ORDER BY time DESC LIMIT %d" % int(limit),
        defaults_file)
    if rows is None:
        return [], err
    # Текст запроса намеренно не забираем: в нём могут оказаться данные
    # пользователей и это ровно то, чему в панели не место.
    return [{"id": r[0], "user": r[1], "db": r[2], "command": r[3],
             "time": int(r[4]) if r[4].isdigit() else 0, "state": r[5]}
            for r in rows if len(r) >= 6], None


def sqlite_info(path, name=None, with_tables=False, limit=20):
    """SQLite открываем только на чтение и через immutable-режим: активный
    писатель со стороны сайта от этого никак не пострадает."""
    info = {"engine": "sqlite", "name": name or os.path.basename(path),
            "path": path, "size": None, "tables": None, "connections": None,
            "error": None, "table_list": [], "system": False}
    if not os.path.exists(path):
        info["error"] = "файл не найден"
        return info
    try:
        info["size"] = os.path.getsize(path)
        info["mtime"] = int(os.path.getmtime(path))
        # WAL и журнал тоже занимают место
        extra = 0
        for suffix in ("-wal", "-shm"):
            if os.path.exists(path + suffix):
                extra += os.path.getsize(path + suffix)
        info["wal_size"] = extra
    except OSError as exc:
        info["error"] = str(exc)
        return info
    try:
        uri = "file:%s?mode=ro&immutable=1" % path.replace("?", "%3f").replace("#", "%23")
        con = sqlite3.connect(uri, uri=True, timeout=5)
        cur = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
        info["tables"] = cur.fetchone()[0]
        info["page_size"] = con.execute("PRAGMA page_size").fetchone()[0]
        info["page_count"] = con.execute("PRAGMA page_count").fetchone()[0]
        freelist = con.execute("PRAGMA freelist_count").fetchone()[0]
        info["free"] = freelist * info["page_size"]
        if with_tables:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            tbls = []
            for tname in names:
                try:
                    n = con.execute('SELECT count(*) FROM "%s"' % tname.replace('"', '')).fetchone()[0]
                except sqlite3.Error:
                    n = None
                try:
                    idx = len(con.execute('PRAGMA index_list("%s")' % tname.replace('"', '')).fetchall())
                except sqlite3.Error:
                    idx = 0
                tbls.append({"name": tname, "rows": n, "indexes": idx})
            # dbstat есть не во всякой сборке; если есть — получим точные размеры
            try:
                sizes = {r[0]: r[1] for r in con.execute(
                    "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name")}
                for t in tbls:
                    t["total_size"] = sizes.get(t["name"])
            except sqlite3.Error:
                for t in tbls:
                    t["total_size"] = None
            tbls.sort(key=lambda t: -(t["total_size"] or 0) if t.get("total_size")
                      else -(t["rows"] or 0))
            info["table_list"] = tbls[:limit]
        con.close()
    except sqlite3.Error as exc:
        info["error"] = str(exc)[:200]
    return info


def collect_all(cfg):
    """Все базы, объявленные у доменов, плюс все базы MySQL сервера."""
    out = []
    defaults = cfg.get("mysql_defaults_file", "/etc/mysql/debian.cnf")
    owner = {}
    for d in cfg.get("domains", []):
        for db in d.get("databases", []):
            key = (db.get("engine"), db.get("name") or db.get("path"))
            owner[key] = d["id"]
    mysql_dbs, mysql_err = mysql_databases(defaults)
    for db in mysql_dbs:
        db["owner"] = owner.get(("mysql", db["name"]))
        db["id"] = "mysql:" + db["name"]
        out.append(db)
    seen = set()
    for d in cfg.get("domains", []):
        for db in d.get("databases", []):
            if db.get("engine") != "sqlite":
                continue
            p = db.get("path")
            if not p or p in seen:
                continue
            seen.add(p)
            info = sqlite_info(p, db.get("name"))
            info["owner"] = d["id"]
            info["id"] = "sqlite:" + p
            out.append(info)
    return out, mysql_err
