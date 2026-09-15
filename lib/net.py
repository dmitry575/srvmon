"""Listening ports.

Parsed straight from /proc/net/* so the result does not depend on the output
format of ss, and so no subprocess is spawned per request.
"""
import os
import socket
import struct

from .sysinfo import _read, mask_cmdline

STATE_LISTEN = "0A"


def _hex_to_addr(hexaddr, family):
    if family == socket.AF_INET:
        return socket.inet_ntop(socket.AF_INET, struct.pack("<I", int(hexaddr, 16)))
    raw = bytes.fromhex(hexaddr)
    swapped = b"".join(struct.pack(">I", struct.unpack("<I", raw[i:i + 4])[0])
                       for i in range(0, 16, 4))
    return socket.inet_ntop(socket.AF_INET6, swapped)


def _inode_map():
    """Map socket inodes to processes, to know who holds a port."""
    mapping = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        name = None
        for fd in fds:
            try:
                link = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if link.startswith("socket:["):
                ino = link[8:-1]
                if name is None:
                    name = (_read(f"/proc/{pid}/comm").strip(),
                            mask_cmdline(_read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()))
                mapping[ino] = {"pid": int(pid), "process": name[0], "cmd": name[1][:180]}
    return mapping


def listening(include_udp=True):
    inodes = _inode_map()
    out = []
    sources = [("/proc/net/tcp", socket.AF_INET, "tcp"),
               ("/proc/net/tcp6", socket.AF_INET6, "tcp6")]
    if include_udp:
        sources += [("/proc/net/udp", socket.AF_INET, "udp"),
                    ("/proc/net/udp6", socket.AF_INET6, "udp6")]
    for path, family, proto in sources:
        text = _read(path)
        for line in text.splitlines()[1:]:
            f = line.split()
            if len(f) < 10:
                continue
            if proto.startswith("tcp") and f[3] != STATE_LISTEN:
                continue
            if proto.startswith("udp") and f[3] != "07":
                continue
            addr_hex, port_hex = f[1].split(":")
            try:
                addr = _hex_to_addr(addr_hex, family)
            except (ValueError, OSError):
                continue
            port = int(port_hex, 16)
            info = inodes.get(f[9], {})
            public = addr in ("0.0.0.0", "::") or not (
                addr.startswith("127.") or addr == "::1")
            # A high-numbered UDP socket is an outbound one (how proxies work),
            # not a service waiting for anybody.
            ephemeral = proto.startswith("udp") and port >= 32768
            out.append({
                "ephemeral": ephemeral,
                "proto": proto, "address": addr, "port": port,
                "pid": info.get("pid"), "process": info.get("process", "?"),
                "cmd": info.get("cmd", ""),
                "scope": "public" if public else "localhost",
            })
    # A port can be open on both v4 and v6 — fold those into one row
    merged = {}
    for row in out:
        key = (row["port"], row["proto"].rstrip("6"), row["pid"])
        cur = merged.get(key)
        if cur:
            cur["addresses"].append(row["address"])
            if row["scope"] == "public":
                cur["scope"] = "public"
        else:
            row["addresses"] = [row["address"]]
            merged[key] = row
    res = sorted(merged.values(), key=lambda r: (r["port"], r["proto"]))
    return res


def annotate(ports, cfg):
    """Label ports with project names from the config so the list reads well."""
    labels = {}
    for d in cfg.get("domains", []):
        if d.get("backend_port"):
            labels[int(d["backend_port"])] = d.get("title") or d["domain"]
    labels.setdefault(80, "nginx HTTP")
    labels.setdefault(443, "nginx HTTPS")
    labels.setdefault(22, "SSH")
    labels.setdefault(3306, "MySQL")
    labels.setdefault(33060, "MySQL X Protocol")
    labels.setdefault(53, "systemd-resolved")
    # Our own label travels as a key: the language is chosen by whoever is
    # looking at the page.
    own_port = None
    try:
        own_port = int(cfg.get("bind_port", 8452))
    except (TypeError, ValueError):
        pass
    for p in ports:
        p["label"] = labels.get(p["port"], "")
        p["label_key"] = "srvmon" if own_port and p["port"] == own_port else None
    return ports
