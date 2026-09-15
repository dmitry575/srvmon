"""System metrics, read straight from /proc and stock utilities.

No psutil: an extra package would cost memory here, and the kernel already
exposes everything needed.
"""
import os
import re
import shutil
import subprocess
import time

_prev_cpu = {}
_prev_net = {}
PAGE = os.sysconf("SC_PAGE_SIZE")
HZ = os.sysconf("SC_CLK_TCK")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def cpu_percent():
    """CPU busy share between two calls. The first call honestly returns None:
    load cannot be derived from a single snapshot."""
    line = _read("/proc/stat").split("\n", 1)[0]
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    prev = _prev_cpu.get("all")
    _prev_cpu["all"] = (idle, total)
    if not prev:
        return None
    d_idle, d_total = idle - prev[0], total - prev[1]
    if d_total <= 0:
        return None
    return round(100.0 * (d_total - d_idle) / d_total, 1)


def meminfo():
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        info[k] = int(v.strip().split()[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    return {
        "total": total, "available": avail, "used": total - avail,
        "free": info.get("MemFree", 0), "buffers": info.get("Buffers", 0),
        "cached": info.get("Cached", 0),
        "percent": round(100.0 * (total - avail) / total, 1) if total else 0.0,
        "swap_total": swap_total, "swap_used": swap_total - swap_free,
        "swap_percent": round(100.0 * (swap_total - swap_free) / swap_total, 1) if swap_total else 0.0,
    }


def loadavg():
    p = _read("/proc/loadavg").split()
    if len(p) < 4:
        return {"load1": 0, "load5": 0, "load15": 0, "procs": 0, "cores": os.cpu_count()}
    running, _, total = p[3].partition("/")
    return {"load1": float(p[0]), "load5": float(p[1]), "load15": float(p[2]),
            "running": int(running), "procs": int(total), "cores": os.cpu_count() or 1}


def uptime():
    try:
        return int(float(_read("/proc/uptime").split()[0]))
    except (ValueError, IndexError):
        return 0


def network():
    """Rate across external interfaces. Loopback and virtual pairs are skipped:
    traffic through the loopback says nothing about the uplink."""
    now = time.time()
    rx = tx = 0
    per_iface = []
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo" or name.startswith(("veth", "docker", "br-")):
            continue
        f = rest.split()
        if len(f) < 9:
            continue
        rx += int(f[0])
        tx += int(f[8])
        per_iface.append({"iface": name, "rx": int(f[0]), "tx": int(f[8])})
    prev = _prev_net.get("all")
    _prev_net["all"] = (rx, tx, now)
    rate_rx = rate_tx = None
    if prev and now > prev[2]:
        dt = now - prev[2]
        rate_rx = max(0.0, (rx - prev[0]) / dt)
        rate_tx = max(0.0, (tx - prev[1]) / dt)
    return {"rx_total": rx, "tx_total": tx, "rx_rate": rate_rx, "tx_rate": rate_tx,
            "interfaces": per_iface}


def temperature():
    """A virtual machine usually has no sensors — then an honest None."""
    best = None
    base = "/sys/class/thermal"
    if os.path.isdir(base):
        for zone in sorted(os.listdir(base)):
            if not zone.startswith("thermal_zone"):
                continue
            raw = _read(os.path.join(base, zone, "temp")).strip()
            if raw.lstrip("-").isdigit():
                val = int(raw) / 1000.0
                if 0 < val < 150 and (best is None or val > best):
                    best = round(val, 1)
    return best


def filesystems():
    out = []
    seen = set()
    for line in _read("/proc/mounts").splitlines():
        f = line.split()
        if len(f) < 3:
            continue
        dev, mount, fstype = f[0], f[1], f[2]
        if fstype in ("proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
                      "securityfs", "pstore", "bpf", "tracefs", "debugfs",
                      "configfs", "fusectl", "mqueue", "hugetlbfs", "autofs",
                      "binfmt_misc", "squashfs", "nsfs", "overlay", "ramfs"):
            continue
        if mount in seen:
            continue
        seen.add(mount)
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        out.append({
            "device": dev, "mount": mount, "fstype": fstype,
            "total": total, "used": used, "free": free,
            "percent": round(100.0 * used / (used + free), 1) if (used + free) else 0.0,
        })
    out.sort(key=lambda x: -x["total"])
    return out


def root_fs():
    for fs in filesystems():
        if fs["mount"] == "/":
            return fs
    return {"total": 0, "used": 0, "free": 0, "percent": 0.0, "mount": "/"}


SECRET_RE = re.compile(
    r"(?i)(--?(?:password|passwd|pass|token|secret|api[-_]?key|auth)[= ]\S+"
    r"|\b[A-Za-z0-9_]*(?:PASSWORD|SECRET|TOKEN|APIKEY|API_KEY)[A-Za-z0-9_]*=\S+)")


def mask_cmdline(cmd):
    """A command line may contain a password — it must not reach the page."""
    return SECRET_RE.sub(lambda m: m.group(0).split("=")[0].split(" ")[0] + "=***", cmd)


def processes(limit=60):
    """Process snapshot with CPU share derived from the tick delta between calls."""
    now = time.time()
    boot = now - uptime()
    result = []
    prev = _prev_cpu.get("procs", {})
    cur = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        stat = _read(f"/proc/{pid}/stat")
        if not stat:
            continue
        try:
            rp = stat.rindex(")")
            name = stat[stat.index("(") + 1:rp]
            f = stat[rp + 2:].split()
            utime_, stime_ = int(f[11]), int(f[12])
            starttime = int(f[19])
            rss = int(f[21]) * PAGE
        except (ValueError, IndexError):
            continue
        ticks = utime_ + stime_
        cur[pid] = (ticks, now)
        cpu = None
        if pid in prev:
            d_t = now - prev[pid][1]
            if d_t > 0.5:
                cpu = round(100.0 * (ticks - prev[pid][0]) / HZ / d_t, 1)
        cmd = _read(f"/proc/{pid}/cmdline").replace("\0", " ").strip() or f"[{name}]"
        try:
            uid = os.stat(f"/proc/{pid}").st_uid
        except OSError:
            uid = -1
        result.append({
            "pid": int(pid), "name": name, "cpu": cpu, "rss": rss,
            "cmd": mask_cmdline(cmd)[:220], "uid": uid,
            "started": int(boot + starttime / HZ),
        })
    _prev_cpu["procs"] = cur
    result.sort(key=lambda p: (-(p["cpu"] or 0), -p["rss"]))
    return result[:limit]


def user_names():
    names = {}
    for line in _read("/etc/passwd").splitlines():
        f = line.split(":")
        if len(f) > 2:
            names[int(f[2])] = f[0]
    return names


def os_release():
    info = {}
    for line in _read("/etc/os-release").splitlines():
        k, _, v = line.partition("=")
        info[k] = v.strip('"')
    return {
        "name": info.get("PRETTY_NAME", "Linux"),
        "kernel": os.uname().release,
        "hostname": os.uname().nodename,
        "arch": os.uname().machine,
        "cpu_model": _cpu_model(),
        "cores": os.cpu_count() or 1,
    }


def _cpu_model():
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def run(cmd, timeout=15):
    """External commands always as an argument list — no shell anywhere."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return 1, "", str(exc)


def du_bytes(path, timeout=120):
    """du is expensive, so the collector calls it rarely and with a timeout."""
    if not os.path.exists(path):
        return None
    if not shutil.which("du"):
        return None
    code, out, _ = run(["du", "-sb", "--one-file-system", path], timeout=timeout)
    if code != 0 or not out.strip():
        return None
    try:
        return int(out.split()[0])
    except (ValueError, IndexError):
        return None
