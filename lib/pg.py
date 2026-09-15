"""PostgreSQL.

Запросы идут через штатный клиент psql, а не через драйвер: панель принципиально
обходится стандартной библиотекой Python, и ставить psycopg ради двух десятков
читающих запросов незачем.

Пароль нигде не хранится. По умолчанию, когда панель работает от root, psql
запускается от системного пользователя postgres — это обычная для Debian и
Ubuntu проверка по владельцу процесса. Если так не подходит, в конфигурации
задаётся строка подключения, а пароль берётся из ~/.pgpass того пользователя,
от которого запущена панель, — то есть остаётся заботой самого PostgreSQL.

Все запросы зашиты в коде. Снаружи SQL не принимается ни в каком виде.
"""
import os
import shutil
import time

from .sysinfo import run

SEP = "\x1f"  # разделитель полей: в данных встретиться не может


def _settings(cfg):
    return (cfg or {}).get("postgres") or {}


def enabled(cfg):
    conf = _settings(cfg)
    if not conf.get("enabled", True):
        return False
    return bool(shutil.which("psql"))


def _command(cfg, sql, dbname=None, timeout=25):
    """Собирает вызов psql. Возвращает (код, вывод, ошибка)."""
    conf = _settings(cfg)
    psql = shutil.which("psql")
    if not psql:
        return 1, "", "клиент psql не найден"
    args = [psql, "--no-psqlrc", "--no-align", "--tuples-only",
            "--field-separator", SEP, "--quiet", "-c", sql]
    dsn = conf.get("dsn")
    if dsn:
        args.insert(1, dsn)
    if dbname:
        args += ["-d", dbname]
    run_as = conf.get("run_as", "postgres")
    if run_as and os.geteuid() == 0:
        # sudo здесь не нужен: мы уже root, а su не требует пароля
        args = ["su", "-s", "/bin/sh", run_as, "-c",
                " ".join(_quote(a) for a in args)]
    env_note = None
    code, out, err = run(args, timeout=timeout)
    return code, out, (err or env_note or "")


def _quote(arg):
    return "'" + str(arg).replace("'", "'\\''") + "'"


def _query(cfg, sql, dbname=None, timeout=25):
    code, out, err = _command(cfg, sql, dbname, timeout)
    if code != 0:
        line = [x for x in (err or "").splitlines() if x.strip()]
        return None, (line[-1][:200] if line else "ошибка запроса к PostgreSQL")
    rows = []
    for raw in out.splitlines():
        if not raw.strip():
            continue
        rows.append(raw.split(SEP))
    return rows, None


def available(cfg):
    rows, err = _query(cfg, "SELECT 1", timeout=10)
    return rows is not None, err


def version(cfg):
    rows, err = _query(cfg, "SHOW server_version", timeout=10)
    if not rows:
        return None
    return rows[0][0]


SYSTEM_DBS = {"template0", "template1", "postgres"}


def databases(cfg):
    """Список баз с размером, числом таблиц и активными соединениями."""
    sql = (
        "SELECT d.datname, pg_database_size(d.datname), "
        " pg_get_userbyid(d.datdba), pg_encoding_to_char(d.encoding), "
        " (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname), "
        " (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname"
        "    AND a.state = 'active'), "
        " coalesce(s.xact_commit, 0), coalesce(s.xact_rollback, 0), "
        " coalesce(s.blks_hit, 0), coalesce(s.blks_read, 0), "
        " coalesce(extract(epoch from s.stats_reset)::bigint, 0) "
        "FROM pg_database d LEFT JOIN pg_stat_database s ON s.datname = d.datname "
        "WHERE datallowconn ORDER BY pg_database_size(d.datname) DESC")
    rows, err = _query(cfg, sql)
    if rows is None:
        return [], err
    out = []
    for r in rows:
        if len(r) < 11:
            continue
        name = r[0]
        hit, read = int(r[8]), int(r[9])
        out.append({
            "engine": "postgres", "name": name, "id": "postgres:" + name,
            "size": int(r[1]), "owner_role": r[2], "encoding": r[3],
            "connections": int(r[4]), "active_queries": int(r[5]),
            "commits": int(r[6]), "rollbacks": int(r[7]),
            "cache_hit_pct": round(100.0 * hit / (hit + read), 1) if (hit + read) else None,
            "stats_reset": int(r[10]) or None,
            "system": name in SYSTEM_DBS,
            "tables": None,   # считается отдельным запросом внутри каждой базы
        })
    # Число таблиц лежит внутри каждой базы: один общий запрос его не даст.
    for db in out:
        if db["system"]:
            continue
        rows, _ = _query(cfg, "SELECT count(*) FROM pg_stat_user_tables", db["name"], timeout=15)
        if rows:
            db["tables"] = int(rows[0][0])
    return out, None


def status(cfg):
    """Общее состояние сервера: соединения, версия, время работы."""
    sql = (
        "SELECT current_setting('server_version'), "
        " current_setting('max_connections'), "
        " (SELECT count(*) FROM pg_stat_activity), "
        " (SELECT count(*) FROM pg_stat_activity WHERE state = 'active'), "
        " (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle'), "
        " (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'), "
        " extract(epoch from (now() - pg_postmaster_start_time()))::bigint, "
        " current_setting('shared_buffers'), "
        " (SELECT count(*) FROM pg_stat_activity WHERE wait_event IS NOT NULL)")
    rows, err = _query(cfg, sql, timeout=15)
    if not rows:
        return {}, err
    r = rows[0]
    if len(r) < 9:
        return {}, "неожиданный ответ сервера"
    return {
        "version": r[0], "max_connections": int(r[1]),
        "connections": int(r[2]), "active": int(r[3]), "idle": int(r[4]),
        "idle_in_transaction": int(r[5]), "uptime": int(r[6]),
        "shared_buffers": r[7], "waiting": int(r[8]),
    }, None


def overview(cfg, dbname):
    """Из чего складывается размер базы: таблицы, индексы, TOAST."""
    sql = (
        "SELECT pg_database_size(current_database()), "
        " coalesce(sum(pg_table_size(c.oid)), 0), "
        " coalesce(sum(pg_indexes_size(c.oid)), 0), "
        " coalesce(sum(CASE WHEN c.reltoastrelid <> 0"
        "   THEN pg_total_relation_size(c.reltoastrelid) ELSE 0 END), 0), "
        " count(*) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')")
    rows, err = _query(cfg, sql, dbname)
    if not rows:
        return {}, err
    r = rows[0]
    table_size = int(r[1])
    toast = int(r[3])
    return {
        "size": int(r[0]), "table_size": table_size - toast if table_size > toast else table_size,
        "index_size": int(r[2]), "toast_size": toast, "tables": int(r[4]),
    }, None


def tables(cfg, dbname, limit=20):
    """Крупнейшие таблицы. Число строк — оценка планировщика: точный счёт
    означал бы полный проход по таблице при каждом обновлении страницы."""
    sql = (
        "SELECT c.relname, "
        " coalesce(s.n_live_tup, 0), "
        " pg_total_relation_size(c.oid), "
        " pg_table_size(c.oid), "
        " pg_indexes_size(c.oid), "
        " CASE WHEN c.reltoastrelid <> 0 THEN pg_total_relation_size(c.reltoastrelid)"
        "      ELSE 0 END, "
        " coalesce(s.n_dead_tup, 0), "
        " coalesce(extract(epoch from greatest(s.last_vacuum, s.last_autovacuum))::bigint, 0), "
        " coalesce(extract(epoch from greatest(s.last_analyze, s.last_autoanalyze))::bigint, 0), "
        " coalesce(s.seq_scan, 0), coalesce(s.idx_scan, 0), n.nspname "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
        "WHERE c.relkind IN ('r','p') "
        "  AND n.nspname NOT IN ('pg_catalog','information_schema') "
        "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT %d" % int(limit))
    rows, err = _query(cfg, sql, dbname)
    if rows is None:
        return [], err
    out = []
    for r in rows:
        if len(r) < 12:
            continue
        total, table_sz, idx, toast = int(r[2]), int(r[3]), int(r[4]), int(r[5])
        out.append({
            "name": r[0], "schema": r[11],
            "rows": int(r[1]), "rows_estimated": True,
            "total_size": total,
            "table_size": table_sz - toast if table_sz > toast else table_sz,
            "index_size": idx, "toast_size": toast,
            "dead_rows": int(r[6]),
            "last_vacuum": int(r[7]) or None,
            "last_analyze": int(r[8]) or None,
            "seq_scans": int(r[9]), "index_scans": int(r[10]),
        })
    return out, None


def activity(cfg, dbname=None, limit=25):
    """Соединения и их состояние. Текст запроса намеренно не забираем:
    в нём могут оказаться данные пользователей, а панели они не нужны."""
    where = "WHERE datname = current_database()" if dbname else ""
    sql = (
        "SELECT pid, coalesce(usename,''), coalesce(datname,''), coalesce(state,''), "
        " coalesce(extract(epoch from (now() - state_change))::bigint, 0), "
        " coalesce(wait_event_type,''), coalesce(wait_event,''), "
        " coalesce(client_addr::text,'local'), coalesce(backend_type,'') "
        "FROM pg_stat_activity %s ORDER BY state_change NULLS LAST LIMIT %d"
        % (where, int(limit)))
    rows, err = _query(cfg, sql, dbname)
    if rows is None:
        return [], err
    return [{
        "pid": int(r[0]) if r[0].isdigit() else None,
        "user": r[1], "db": r[2], "state": r[3] or "—",
        "seconds": int(r[4]), "wait": (r[5] + "/" + r[6]).strip("/"),
        "client": r[7], "backend_type": r[8],
    } for r in rows if len(r) >= 9], None


def indexes(cfg, dbname, limit=10):
    """Самые большие индексы: они часто и есть причина роста базы."""
    sql = (
        "SELECT indexrelname, relname, pg_relation_size(indexrelid), "
        " coalesce(idx_scan, 0) FROM pg_stat_user_indexes "
        "ORDER BY pg_relation_size(indexrelid) DESC LIMIT %d" % int(limit))
    rows, err = _query(cfg, sql, dbname)
    if rows is None:
        return [], err
    return [{"name": r[0], "table": r[1], "size": int(r[2]), "scans": int(r[3])}
            for r in rows if len(r) >= 4], None


def database_detail(cfg, dbname, limit=20):
    info = {"engine": "postgres", "name": dbname, "id": "postgres:" + dbname}
    ov, err = overview(cfg, dbname)
    if err:
        info["error"] = err
        return info
    info.update(ov)
    info["tables_list"], info["tables_error"] = tables(cfg, dbname, limit)
    info["processlist"], _ = activity(cfg, dbname)
    info["indexes"], _ = indexes(cfg, dbname)
    st, _ = status(cfg)
    info["server_status"] = st
    info["connections"] = sum(1 for p in info["processlist"] if p["db"] == dbname)
    info["rows"] = sum(t["rows"] for t in info["tables_list"])
    info["free"] = sum(t["dead_rows"] for t in info["tables_list"])
    return info
