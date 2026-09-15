"""State of systemd units.

Read-only: the dashboard has no start, stop or restart. There is no path
through it that runs systemctl on someone else's behalf.
"""
import os
import time

from .sysinfo import run, _read, PAGE, HZ, uptime as sys_uptime

def _num(val):
    """systemd returns "[not set]" and "infinity" — neither survives int()."""
    if val is None:
        return None
    val = val.strip()
    return int(val) if val.isdigit() else None


PROPS = ["Id", "Description", "ActiveState", "SubState", "UnitFileState",
         "MainPID", "ExecMainStartTimestampMonotonic", "NRestarts",
         "MemoryCurrent", "CPUUsageNSec", "Result", "StatusText"]


def unit_status(name):
    code, out, err = run(["systemctl", "show", name, "--no-page",
                          "--property=" + ",".join(PROPS)], timeout=10)
    data = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        data[k] = v
    if not data:
        return {"name": name, "active": "unknown", "error": err.strip()[:200] or "unit not found"}
    pid = _num(data.get("MainPID")) or 0
    started = None
    mono = data.get("ExecMainStartTimestampMonotonic")
    mono_v = _num(mono)
    if mono_v and mono_v > 0:
        started = int(time.time() - sys_uptime() + mono_v / 1_000_000)
    rss = _num(data.get("MemoryCurrent"))
    if rss is None and pid:
        rss = _proc_rss(pid)
    res = {
        "name": name,
        "description": data.get("Description", ""),
        "active": data.get("ActiveState", "unknown"),
        "sub": data.get("SubState", ""),
        "enabled": data.get("UnitFileState", ""),
        "pid": pid or None,
        "rss": rss,
        "restarts": _num(data.get("NRestarts")) or 0,
        "started": started,
        "uptime": int(time.time() - started) if started else None,
        "result": data.get("Result", ""),
        "status_text": data.get("StatusText", "")[:200],
        "cpu_nsec": _num(data.get("CPUUsageNSec")) or None,
    }
    res["ok"] = res["active"] == "active"
    return res


def _proc_rss(pid):
    stat = _read(f"/proc/{pid}/stat")
    if not stat:
        return None
    try:
        f = stat[stat.rindex(")") + 2:].split()
        return int(f[21]) * PAGE
    except (ValueError, IndexError):
        return None


def unit_journal(name, lines=25):
    code, out, err = run(["journalctl", "-u", name, "-n", str(int(lines)),
                          "--no-pager", "-o", "short-iso"], timeout=15)
    if code != 0:
        return []
    return out.splitlines()


def failed_units():
    code, out, _ = run(["systemctl", "list-units", "--type=service", "--state=failed",
                        "--no-pager", "--no-legend", "--plain"], timeout=10)
    res = []
    for line in out.splitlines():
        f = line.split()
        if f:
            res.append(f[0])
    return res


def collect(cfg):
    names = []
    for d in cfg.get("domains", []):
        svc = d.get("service")
        if svc:
            names.append(svc)
    names += cfg.get("extra_services", [])
    names.append("srvmon.service")
    names.append("srvmon-collector.service")
    seen, out = set(), []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out.append(unit_status(n))
    failed = set(failed_units())
    for u in out:
        if u["name"] in failed:
            u["active"] = "failed"
            u["ok"] = False
    return out
