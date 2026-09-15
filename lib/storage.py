"""Storage: filesystems, sizes of watched directories, directory browsing.

Browsing is strictly read-only: nothing is deleted, nothing is written, and
file contents are never read. Escaping the allowed roots is not possible —
every path is resolved through realpath before being checked against the
allowlist, so a symlink pointing outside does not help.
"""
import os
import time

from .sysinfo import du_bytes, filesystems

DEFAULT_ROOTS = ["/root", "/home", "/srv", "/opt", "/var/www", "/var/log",
                 "/var/lib/mysql", "/var/cache", "/etc/nginx"]

# Names flagged in the directory browser and never opened. This is not a
# protection in itself — file contents are never read at all — but a hint for
# the eye. Add your own through "sensitive_names" in config.json.
DEFAULT_DENY = {
    "privkey.pem", "private.key", "server.key", ".env", ".env.local",
    "id_rsa", "id_ed25519", "id_ecdsa", ".my.cnf", ".pgpass", ".netrc",
    "credentials", "secrets.json", "secret.txt", "htpasswd", ".htpasswd",
    "shadow", "authorized_keys", "known_hosts", "users.json",
}

ALLOWED_ROOTS = list(DEFAULT_ROOTS)
DENY_NAMES = set(DEFAULT_DENY)


def configure(cfg):
    """Take browsable roots and flagged names from the configuration when
    they are set there; otherwise keep sensible defaults."""
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
    """Return the normalised path, or None if it lies outside the roots."""
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
        return {"error": "path is outside the allowed directories", "path": path}
    if not os.path.isdir(real):
        return {"error": "not a directory", "path": real}
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
        return {"error": "no permission to read the directory", "path": real}
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
    """Run du over the watched directories. Called rarely: across tens of
    gigabytes a full walk costs real time and disk operations."""
    out = []
    for p in paths:
        size = du_bytes(p, timeout=timeout)
        out.append({"path": p, "bytes": size,
                    "exists": os.path.exists(p), "ts": int(time.time())})
    return out


def top_subdirs(path, limit=12, timeout=90):
    """Largest subdirectories one level down — to find where space went."""
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
