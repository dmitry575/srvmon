"""Разбор access-логов nginx.

Логи ведутся в формате combined, времени ответа в них нет. Поэтому отсюда
берутся только те величины, которые в логе действительно есть: число запросов,
распределение статусов и отданные байты. Время ответа панель берёт из
собственных замеров в health-проверках и нигде не смешивает одно с другим.
Если формат лога когда-нибудь расширят полями $request_time/$upstream_response_time,
парсер подхватит их автоматически — они ищутся в хвосте строки.
"""
import os
import re
import time
from calendar import timegm

from . import store

# combined: ip - user [time] "request" status bytes "referer" "ua"
LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<req>[^"]*)" '
    r'(?P<status>\d{3}) (?P<bytes>\d+|-)(?P<tail>.*)$')
# хвост расширенного формата: rt=0.123 urt=0.120 либо два последних числа
TAIL_RT_RE = re.compile(r'\brt=(?P<rt>[\d.]+)')
TAIL_URT_RE = re.compile(r'\burt=(?P<urt>[\d.]+)')

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

BOT_RE = re.compile(r"(?i)bot|crawler|spider|slurp|yandex|googlebot|bingbot|claudebot|gptbot")


def parse_time(raw):
    """14/Sep/2026:16:54:57 +0200 -> unix time."""
    try:
        date_part, _, tz = raw.partition(" ")
        d, mon, rest = date_part.split("/", 2)
        y, hh, mm, ss = rest.split(":")
        base = timegm((int(y), MONTHS[mon], int(d), int(hh), int(mm), int(ss), 0, 0, 0))
        if tz and len(tz) == 5:
            off = (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60) * (-1 if tz[0] == "+" else 1)
            base += off
        return base
    except (ValueError, KeyError, IndexError):
        return None


class Tailer:
    """Держит позицию в файле и отдаёт только дописанные строки.
    Смена inode или усечение файла означают ротацию — читаем с начала."""

    def __init__(self, path):
        self.path = path

    def _pos(self):
        row = store.one("SELECT inode, offset FROM logpos WHERE path=?", (self.path,))
        return (row["inode"], row["offset"]) if row else (None, None)

    def _save(self, inode, offset):
        store.write("INSERT OR REPLACE INTO logpos(path, inode, offset) VALUES(?,?,?)",
                    (self.path, inode, offset))

    def read_new(self, max_bytes=12 * 1024 * 1024):
        if not os.path.exists(self.path):
            return []
        st = os.stat(self.path)
        inode, offset = self._pos()
        start = 0
        if inode == st.st_ino and offset is not None and offset <= st.st_size:
            start = offset
        elif inode == st.st_ino and offset is not None and offset > st.st_size:
            start = 0  # файл усечён
        elif inode is None:
            # первый запуск: не перемалываем гигабайты истории, берём только хвост
            start = max(0, st.st_size - 512 * 1024)
        if st.st_size - start > max_bytes:
            start = st.st_size - max_bytes
        lines = []
        with open(self.path, "rb") as fh:
            fh.seek(start)
            data = fh.read(st.st_size - start)
            end = fh.tell()
        text = data.decode("utf-8", "replace")
        if text and not text.endswith("\n"):
            cut = text.rfind("\n")
            if cut >= 0:
                end -= len(text[cut + 1:].encode("utf-8", "replace"))
                text = text[:cut + 1]
        lines = text.splitlines()
        self._save(st.st_ino, end)
        return lines


def aggregate(lines, since=None):
    """Сводка по пачке строк. Отдаёт счётчики и список ошибок 5xx."""
    agg = {"requests": 0, "c2xx": 0, "c3xx": 0, "c4xx": 0, "c5xx": 0,
           "bytes": 0, "bots": 0, "rt_sum": 0.0, "rt_n": 0,
           "first_ts": None, "last_ts": None}
    errors = []
    paths = {}
    for line in lines:
        m = LINE_RE.match(line)
        if not m:
            continue
        ts = parse_time(m.group("ts"))
        if since and ts and ts < since:
            continue
        status = int(m.group("status"))
        nbytes = m.group("bytes")
        agg["requests"] += 1
        agg["bytes"] += int(nbytes) if nbytes.isdigit() else 0
        bucket = "c%dxx" % (status // 100)
        if bucket in agg:
            agg[bucket] += 1
        if BOT_RE.search(line):
            agg["bots"] += 1
        tail = m.group("tail") or ""
        rt = TAIL_RT_RE.search(tail)
        if rt:
            agg["rt_sum"] += float(rt.group("rt"))
            agg["rt_n"] += 1
        if ts:
            agg["first_ts"] = ts if agg["first_ts"] is None else min(agg["first_ts"], ts)
            agg["last_ts"] = ts if agg["last_ts"] is None else max(agg["last_ts"], ts)
        req = m.group("req")
        path = req.split(" ")[1] if " " in req else req
        if status >= 400:
            errors.append({"ts": ts or int(time.time()), "status": status,
                           "path": path[:200], "line": line[:400]})
        paths[path[:120]] = paths.get(path[:120], 0) + 1
    agg["top_paths"] = sorted(paths.items(), key=lambda kv: -kv[1])[:15]
    agg["avg_rt_ms"] = round(1000.0 * agg["rt_sum"] / agg["rt_n"], 1) if agg["rt_n"] else None
    return agg, errors


def scan_history(path, since_ts, max_bytes=40 * 1024 * 1024):
    """Разовый проход по хвосту файла — нужен при старте, чтобы графики
    за сутки не начинались с пустоты."""
    if not os.path.exists(path):
        return []
    st = os.stat(path)
    start = max(0, st.st_size - max_bytes)
    with open(path, "rb") as fh:
        fh.seek(start)
        text = fh.read().decode("utf-8", "replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines


def bucket_history(lines, bucket_sec=300, since=None):
    """Раскладывает строки по интервалам — для восстановления графика из лога."""
    out = {}
    for line in lines:
        m = LINE_RE.match(line)
        if not m:
            continue
        ts = parse_time(m.group("ts"))
        if ts is None or (since and ts < since):
            continue
        key = ts - (ts % bucket_sec)
        b = out.setdefault(key, {"requests": 0, "c2xx": 0, "c3xx": 0, "c4xx": 0,
                                 "c5xx": 0, "bytes": 0})
        status = int(m.group("status"))
        b["requests"] += 1
        nb = m.group("bytes")
        b["bytes"] += int(nb) if nb.isdigit() else 0
        k = "c%dxx" % (status // 100)
        if k in b:
            b[k] += 1
    return out
