#!/usr/bin/env bash
# Install the dashboard: systemd units, configuration, first user.
# It settles next to whatever already runs on the server: nothing else is
# touched, no port but its own is taken, no existing config is rewritten.
#
#   sudo ./install.sh                     install and start
#   sudo ./install.sh --port 9000         another port for the local server
#   sudo ./install.sh --user bob          another login
#   sudo ./install.sh --no-start          prepare only, do not start
#   sudo ./install.sh --uninstall         remove the units (data is kept)

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8452
USERNAME=admin
PASSWORD=""
START=1
UNINSTALL=0
PYTHON="$(command -v python3 || true)"
# The unit directory is a variable so the install test can write to a temporary
# place without touching what already runs on the machine.
UNIT_DIR="${SRVMON_UNIT_DIR:-/etc/systemd/system}"

# Message language comes from the environment: this is run by Russian and
# non-Russian speakers alike.
if [[ "${LANG:-}" == ru* || "${LC_ALL:-}" == ru* ]]; then RU=1; else RU=0; fi
say() { if [[ $RU -eq 1 ]]; then echo -e "$1"; else echo -e "$2"; fi; }
die() { say "✗ $1" "✗ $2" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?}"; shift 2;;
    --user) USERNAME="${2:?}"; shift 2;;
    --password) PASSWORD="${2:?}"; shift 2;;
    --no-start) START=0; shift;;
    --uninstall) UNINSTALL=1; shift;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) die "Неизвестный ключ: $1" "Unknown option: $1";;
  esac
done

[[ $EUID -eq 0 || -n "${SRVMON_UNIT_DIR:-}" ]] || die "Запускать нужно от root: sudo ./install.sh" \
                         "Run as root: sudo ./install.sh"

if [[ $UNINSTALL -eq 1 ]]; then
  say "Останавливаю службы…" "Stopping services…"
  systemctl disable --now srvmon.service srvmon-collector.service 2>/dev/null || true
  rm -f "$UNIT_DIR/srvmon.service" "$UNIT_DIR/srvmon-collector.service"
  systemctl daemon-reload
  say "Готово. Каталог $DIR и собранные метрики не тронуты." \
      "Done. The $DIR directory and collected metrics are left intact."
  exit 0
fi

# --- environment checks ---
[[ -n "$PYTHON" ]] || die "Нужен python3 (3.8 или новее)" "python3 (3.8+) is required"
"$PYTHON" - <<'PY' || die "Нужен Python 3.8 или новее" "Python 3.8 or newer is required"
import sys
sys.exit(0 if sys.version_info >= (3, 8) else 1)
PY
command -v systemctl >/dev/null || die "Нужен systemd" "systemd is required"

say "Панель: $DIR" "Dashboard: $DIR"
say "Python: $($PYTHON -V 2>&1)" "Python: $($PYTHON -V 2>&1)"

# --- configuration ---
mkdir -p "$DIR/var"
chmod 700 "$DIR/var"
if [[ ! -f "$DIR/config.json" ]]; then
  cp "$DIR/config.example.json" "$DIR/config.json"
  "$PYTHON" - "$DIR/config.json" "$PORT" <<'PY'
import json, sys
path, port = sys.argv[1], int(sys.argv[2])
cfg = json.load(open(path, encoding='utf-8'))
cfg['bind_port'] = port
# The example carries a demonstration domain: disabled, but better gone.
cfg['domains'] = []
json.dump(cfg, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
PY
  say "Создана конфигурация: config.json (доменов пока нет)" \
      "Created configuration: config.json (no domains yet)"
else
  say "Конфигурация уже есть, оставляю как есть: config.json" \
      "Configuration already exists, keeping it: config.json"
  PORT="$("$PYTHON" -c "import json;print(json.load(open('$DIR/config.json'))['bind_port'])")"
fi

# --- the port must be free ---
if ss -tln 2>/dev/null | grep -q ":$PORT "; then
  OWNER="$(ss -tlnp 2>/dev/null | awk -v p=":$PORT " '$4 ~ p {print $6}' | head -1)"
  die "Порт $PORT уже занят ($OWNER). Укажите другой: --port ХХХХ" \
      "Port $PORT is already in use ($OWNER). Pick another one: --port NNNN"
fi

# --- first user ---
if [[ ! -f "$DIR/var/users.json" ]]; then
  if [[ -z "$PASSWORD" ]]; then
    PASSWORD="$("$PYTHON" -c "
import secrets, string
alphabet = string.ascii_letters + string.digits
print(''.join(secrets.choice(alphabet) for _ in range(18)))")"
    GENERATED=1
  fi
  "$PYTHON" "$DIR/tools/passwd.py" "$USERNAME" "$PASSWORD" >/dev/null
  chmod 600 "$DIR/var/users.json"
else
  say "Пользователь уже заведён, пароль не меняю." "User already exists, password left as is."
fi

# --- systemd units ---
write_unit() {
  local name="$1" desc_ru="$2" desc_en="$3" exec="$4" extra="$5"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/$name" <<UNIT
[Unit]
Description=$( [[ $RU -eq 1 ]] && echo "$desc_ru" || echo "$desc_en" )
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$PYTHON -u $DIR/$exec
Restart=always
RestartSec=5
# The dashboard has to be cheaper than what it watches. MemoryHigh is a soft
# limit: crossing it makes the kernel reclaim the file cache first, and only
# MemoryMax stops real growth.
MemoryHigh=96M
MemoryMax=320M
$extra
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${name%.service}

[Install]
WantedBy=multi-user.target
UNIT
}

write_unit "srvmon.service" \
  "Панель мониторинга сервера — веб-часть (127.0.0.1:$PORT)" \
  "Server monitoring dashboard — web (127.0.0.1:$PORT)" \
  "server.py" "Nice=5"

write_unit "srvmon-collector.service" \
  "Панель мониторинга сервера — сбор метрик" \
  "Server monitoring dashboard — metrics collector" \
  "collector.py" "Nice=10
IOSchedulingClass=idle
CPUWeight=20"

if [[ "$UNIT_DIR" == "/etc/systemd/system" ]]; then
  systemctl daemon-reload
fi
say "Службы systemd установлены." "systemd units installed."

if [[ $START -eq 1 ]]; then
  systemctl enable --now srvmon-collector.service srvmon.service >/dev/null 2>&1
  sleep 2
  for unit in srvmon srvmon-collector; do
    if ! systemctl is-active --quiet "$unit"; then
      say "✗ Служба $unit не запустилась. Журнал: journalctl -u $unit -n 30" \
          "✗ Unit $unit failed to start. Log: journalctl -u $unit -n 30"
      exit 1
    fi
  done
  say "Службы запущены и включены в автозапуск." "Services started and enabled on boot."
fi

# --- summary ---
echo
say "── Готово ──" "── Done ──"
say "Панель слушает: http://127.0.0.1:$PORT" "Dashboard listens on: http://127.0.0.1:$PORT"
say "Логин: $USERNAME" "Login: $USERNAME"
[[ "${GENERATED:-0}" -eq 1 ]] && say "Пароль: $PASSWORD  (сохраните, он больше не покажется)" \
                                     "Password: $PASSWORD  (save it, it will not be shown again)"
echo
say "Дальше:" "Next steps:"
say "  1. Посмотреть, что есть на сервере:   $PYTHON $DIR/tools/discover.py" \
    "  1. See what this server runs:          $PYTHON $DIR/tools/discover.py"
say "  2. Открыть панель снаружи через nginx: см. nginx.example.conf" \
    "  2. Expose it through nginx:            see nginx.example.conf"
say "  3. Добавить сайты в разделе Settings — сами они не появятся." \
    "  3. Add your sites in Settings — nothing is monitored automatically."
echo
say "Проверка: SRVMON_PASSWORD='…' $PYTHON $DIR/tools/selftest.py" \
    "Self-test: SRVMON_PASSWORD='…' $PYTHON $DIR/tools/selftest.py"
