"""Диск: разделы, размеры наблюдаемых каталогов и просмотр дерева.

Просмотр строго читающий: ни удаления, ни записи, ни чтения содержимого
файлов. Выход за пределы разрешённых корней невозможен — путь разворачивается
через realpath и сверяется с белым списком, так что симлинк наружу не помогает.
"""
import os
import time

from .sysinfo import du_bytes, filesystems

DEFAULT_ROOTS = ["/root", "/home", "/srv", "/opt", "/var/www", "/var/log",
                 "/var/lib/mysql", "/var/cache", "/etc/nginx"]

# Имена, которые в браузере каталогов помечаются и не открываются. Это не
# защита сама по себе — содержимое файлов не читается вовсе, — а подсказка
# глазу. Свои имена добавляются в config.json ключом "sensitive_names".
DEFAULT_DENY = {
    "privkey.pem", "private.key", "server.key", ".env", ".env.local",
    "id_rsa", "id_ed25519", "id_ecdsa", ".my.cnf", ".pgpass", ".netrc",
    "credentials", "secrets.json", "secret.txt", "htpasswd", ".htpasswd",
    "shadow", "authorized_keys", "known_hosts", "users.json",
}

ALLOWED_ROOTS = list(DEFAULT_ROOTS)
DENY_NAMES = set(DEFAULT_DENY)


def configure(cfg):
    """Разрешённые для просмотра корни и помечаемые имена берутся из
    конфигурации, если они там заданы; иначе остаются разумные значения."""
    global ALLOWED_ROOTS, DENY_NAMES
    roots = cfg.get("browse_roots")
    if isinstance(roots, list) and roots:
        ALLOWED_ROOTS = [r for r in roots if isinstance(r, str) and r.startswith("/")]
    else:
        ALLOWED_ROOTS = list(DEFAULT_ROOTS)
    extra = cfg.get("sensitive_names")
    DENY_NAMES = set(DEFAULT_DENY)
    if isinstance(extra, list):
        DENY_NAMES |= {str(x) for x in extra}


def safe_path(path):
    """Возвращает нормализованный путь либо None, если он вне разрешённых корней."""
    if not path:
        return None
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    for root in ALLOWED_ROOTS:
        if real == root or real.startswith(root.rstrip("/") + "/"):
            return real
    return None


def listdir(path, limit=400):
    real = safe_path(path)
    if not real:
        return {"error": "путь вне разрешённых каталогов", "path": path}
    if not os.path.isdir(real):
        return {"error": "это не каталог", "path": real}
    entries = []
    try:
        with os.scandir(real) as it:
            for e in it:
                name = e.name
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                entries.append({
                    "name": name,
                    "dir": is_dir,
                    "size": None if is_dir else st.st_size,
                    "mtime": int(st.st_mtime),
                    "sensitive": name in DENY_NAMES,
                })
    except PermissionError:
        return {"error": "нет прав на чтение каталога", "path": real}
    entries.sort(key=lambda e: (not e["dir"], -(e["size"] or 0), e["name"]))
    parent = os.path.dirname(real)
    return {
        "path": real,
        "parent": parent if safe_path(parent) and real not in ALLOWED_ROOTS else None,
        "entries": entries[:limit],
        "truncated": len(entries) > limit,
        "count": len(entries),
    }


def dir_sizes(paths, timeout=90):
    """du по списку наблюдаемых каталогов. Зовётся редко: на 40 ГБ данных
    полный обход стоит заметного времени и дисковых операций."""
    out = []
    for p in paths:
        size = du_bytes(p, timeout=timeout)
        out.append({"path": p, "bytes": size,
                    "exists": os.path.exists(p), "ts": int(time.time())})
    return out


def top_subdirs(path, limit=12, timeout=90):
    """Крупнейшие подкаталоги одного уровня — чтобы найти, где ушло место."""
    real = safe_path(path)
    if not real or not os.path.isdir(real):
        return []
    out = []
    try:
        with os.scandir(real) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    size = du_bytes(e.path, timeout=timeout)
                    if size is not None:
                        out.append({"path": e.path, "name": e.name, "bytes": size})
    except OSError:
        return []
    out.sort(key=lambda d: -d["bytes"])
    return out[:limit]


def overview(cfg):
    fs = filesystems()
    root = next((f for f in fs if f["mount"] == "/"), fs[0] if fs else None)
    return {"filesystems": fs, "root": root}
