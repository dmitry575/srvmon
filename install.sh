#!/usr/bin/env bash
# srvmon — server dashboard for a small VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/dmitry575/srvmon/main/install.sh | sudo bash
#
# Run from a clone instead, and it installs what is next to it. Either way the
# result is the same: two systemd units, a self-signed certificate, a generated
# password, and an address you can open right away.
#
#   --port 8452         port the dashboard listens on
#   --user admin        login to create
#   --password ...      password to use instead of a generated one
#   --bind 0.0.0.0      what to listen on (127.0.0.1 keeps it local)
#   --dir /opt/srvmon   where to install when fetching over the network
#   --local             do not ask for TLS; bind to 127.0.0.1 only
#   --no-start          prepare everything but do not start
#   --uninstall         remove the units (collected data is kept)

set -euo pipefail

REPO="${SRVMON_REPO:-dmitry575/srvmon}"
PORT=8452
USERNAME=admin
PASSWORD=""
BIND=""
DIR=""
START=1
UNINSTALL=0
LOCAL_ONLY=0
UNIT_DIR="${SRVMON_UNIT_DIR:-/etc/systemd/system}"

if [[ "${LANG:-}" == ru* || "${LC_ALL:-}" == ru* ]]; then RU=1; else RU=0; fi
say() { if [[ $RU -eq 1 ]]; then echo -e "$1"; else echo -e "$2"; fi; }
die() { say "✗ $1" "✗ $2" >&2; exit 1; }
step() { if [[ $RU -eq 1 ]]; then echo -e "\n── $1"; else echo -e "\n── $2"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?}"; shift 2;;
    --user) USERNAME="${2:?}"; shift 2;;
    --password) PASSWORD="${2:?}"; shift 2;;
    --bind) BIND="${2:?}"; shift 2;;
    --dir) DIR="${2:?}"; shift 2;;
    --local) LOCAL_ONLY=1; shift;;
    --no-start) START=0; shift;;
    --uninstall) UNINSTALL=1; shift;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) die "Неизвестный ключ: $1" "Unknown option: $1";;
  esac
done

[[ $EUID -eq 0 || -n "${SRVMON_UNIT_DIR:-}" ]] || die \
  "Запускать нужно от root: sudo ./install.sh" "Run as root: sudo ./install.sh"

if [[ $UNINSTALL -eq 1 ]]; then
  systemctl disable --now srvmon.service srvmon-collector.service 2>/dev/null || true
  rm -f "$UNIT_DIR/srvmon.service" "$UNIT_DIR/srvmon-collector.service"
  systemctl daemon-reload 2>/dev/null || true
  say "Службы удалены. Каталог и собранные метрики не тронуты." \
      "Units removed. The directory and collected metrics are left intact."
  exit 0
fi

# ---------------------------------------------------------------- environment
PYTHON="$(command -v python3 || true)"
[[ -n "$PYTHON" ]] || die "Нужен python3 (3.8 или новее)" "python3 (3.8+) is required"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "Нужен Python 3.8 или новее" "Python 3.8 or newer is required"
command -v systemctl >/dev/null || die "Нужен systemd" "systemd is required"

# ------------------------------------------------------------------ the files
# Piped from curl there is no source next to the script, so fetch a copy.
SELF_DIR=""
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
  SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "$SELF_DIR" && -f "$SELF_DIR/server.py" ]]; then
  DIR="${DIR:-$SELF_DIR}"
  if [[ "$DIR" != "$SELF_DIR" ]]; then
    mkdir -p "$DIR"
    cp -r "$SELF_DIR/." "$DIR/"
  fi
  step "Ставлю из текущего каталога" "Installing from the current directory"
else
  DIR="${DIR:-/opt/srvmon}"
  step "Скачиваю srvmon" "Downloading srvmon"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  URL="https://api.github.com/repos/$REPO/releases/latest"
  TARBALL="$("$PYTHON" - "$URL" <<'PY' || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=30) as r:
        data = json.load(r)
    for asset in data.get("assets", []):
        if asset["name"].endswith(".tar.gz"):
            print(asset["browser_download_url"]); break
except Exception:
    pass
PY
)"
  if [[ -n "$TARBALL" ]]; then
    curl -fsSL --retry 3 "$TARBALL" -o "$TMP/srvmon.tar.gz" \
      || die "Не удалось скачать архив" "Could not download the archive"
  else
    # No release yet, or the API is unreachable: take the branch instead.
    curl -fsSL --retry 3 "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" \
      -o "$TMP/srvmon.tar.gz" || die "Не удалось скачать исходники" "Could not download the sources"
  fi
  mkdir -p "$TMP/x" && tar -C "$TMP/x" -xzf "$TMP/srvmon.tar.gz"
  SRC="$(find "$TMP/x" -maxdepth 2 -name server.py -printf '%h\n' | head -1)"
  [[ -n "$SRC" ]] || die "В архиве нет server.py" "server.py is missing from the archive"
  mkdir -p "$DIR"
  cp -r "$SRC/." "$DIR/"
  say "  распаковано в $DIR" "  unpacked into $DIR"
fi

cd "$DIR"
chmod +x install.sh 2>/dev/null || true
mkdir -p var && chmod 700 var

# --------------------------------------------------------------- how to reach
detect_ip() {
  local ip
  ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
  [[ -z "$ip" ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "$ip"
}
PUBLIC_IP="$(detect_ip)"

if [[ $LOCAL_ONLY -eq 1 ]]; then
  BIND="127.0.0.1"
elif [[ -z "$BIND" ]]; then
  BIND="0.0.0.0"
fi
PUBLIC=0
[[ "$BIND" != "127.0.0.1" && "$BIND" != "localhost" ]] && PUBLIC=1

# ------------------------------------------------------------------ TLS certs
CERT_DIR="$DIR/var/tls"
if [[ $PUBLIC -eq 1 ]]; then
  step "Готовлю HTTPS" "Preparing HTTPS"
  if [[ -f "$CERT_DIR/cert.pem" && -f "$CERT_DIR/key.pem" ]]; then
    say "  сертификат уже есть" "  certificate already present"
  else
    command -v openssl >/dev/null || die \
      "Нужен openssl, либо ставьте с ключом --local" \
      "openssl is required, or install with --local"
    mkdir -p "$CERT_DIR"
    # Self-signed and issued for the address people will actually type, so the
    # browser warning is about the issuer only — not about the wrong name.
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
      -subj "/CN=${PUBLIC_IP:-srvmon}" \
      -addext "subjectAltName=IP:${PUBLIC_IP:-127.0.0.1},DNS:localhost" \
      >/dev/null 2>&1 || die "openssl не смог создать сертификат" \
                             "openssl could not create the certificate"
    chmod 600 "$CERT_DIR/key.pem"
    say "  самоподписанный сертификат создан на 10 лет" \
        "  self-signed certificate created, valid for 10 years"
  fi
fi

# ---------------------------------------------------------------- config file
step "Настраиваю" "Configuring"
if [[ ! -f config.json ]]; then
  cp config.example.json config.json
  "$PYTHON" - config.json "$PORT" "$BIND" "$PUBLIC" "$CERT_DIR" <<'PY'
import json, sys
path, port, bind, public, certdir = sys.argv[1:6]
cfg = json.load(open(path, encoding='utf-8'))
cfg['bind_port'] = int(port)
cfg['bind_host'] = bind
cfg['domains'] = []          # the example domain is a placeholder, not yours
if public == '1':
    cfg['tls'] = {'enabled': True,
                  'cert': certdir + '/cert.pem',
                  'key': certdir + '/key.pem'}
json.dump(cfg, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
PY
  say "  создан config.json (доменов пока нет)" "  created config.json (no domains yet)"
else
  say "  config.json уже есть, оставляю как есть" "  config.json already exists, keeping it"
  PORT="$("$PYTHON" -c "import json;print(json.load(open('config.json'))['bind_port'])")"
  BIND="$("$PYTHON" -c "import json;print(json.load(open('config.json')).get('bind_host','127.0.0.1'))")"
fi

if ss -tln 2>/dev/null | grep -qE "[:.]$PORT\b"; then
  die "Порт $PORT занят. Укажите другой: --port ХХХХ" \
      "Port $PORT is in use. Pick another one: --port NNNN"
fi

# ------------------------------------------------------------------- the user
GENERATED=0
if [[ ! -f var/users.json ]]; then
  if [[ -z "$PASSWORD" ]]; then
    PASSWORD="$("$PYTHON" -c "
import secrets, string
a = string.ascii_letters + string.digits
print(''.join(secrets.choice(a) for _ in range(18)))")"
    GENERATED=1
  fi
  "$PYTHON" tools/passwd.py "$USERNAME" "$PASSWORD" >/dev/null
  chmod 600 var/users.json
else
  say "  пользователь уже заведён" "  user already exists"
fi

# ----------------------------------------------------------------- the units
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
  "Панель мониторинга сервера — веб-часть" \
  "Server monitoring dashboard — web" "server.py" "Nice=5"
write_unit "srvmon-collector.service" \
  "Панель мониторинга сервера — сбор метрик" \
  "Server monitoring dashboard — metrics collector" "collector.py" \
  "Nice=10
IOSchedulingClass=idle
CPUWeight=20"

[[ "$UNIT_DIR" == "/etc/systemd/system" ]] && systemctl daemon-reload
say "  службы systemd установлены" "  systemd units installed"

# ------------------------------------------------------------------- firewall
if [[ $PUBLIC -eq 1 && "$UNIT_DIR" == "/etc/systemd/system" ]]; then
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null 2>&1 && \
      say "  порт $PORT открыт в ufw" "  port $PORT opened in ufw"
  fi
fi

# ---------------------------------------------------------------------- start
if [[ $START -eq 1 && "$UNIT_DIR" == "/etc/systemd/system" ]]; then
  step "Запускаю" "Starting"
  systemctl enable --now srvmon-collector.service srvmon.service >/dev/null 2>&1
  sleep 3
  for unit in srvmon srvmon-collector; do
    systemctl is-active --quiet "$unit" || {
      say "  ✗ $unit не запустилась. Журнал: journalctl -u $unit -n 30" \
          "  ✗ $unit failed to start. Log: journalctl -u $unit -n 30"
      exit 1
    }
  done
  say "  работает, автозапуск включён" "  running, enabled on boot"
fi

# --------------------------------------------------------------------- result
SCHEME=http
[[ $PUBLIC -eq 1 ]] && SCHEME=https
ADDR="$SCHEME://${PUBLIC_IP:-127.0.0.1}:$PORT"
[[ $PUBLIC -eq 0 ]] && ADDR="http://127.0.0.1:$PORT"

echo
say "══ Готово ══" "══ Done ══"
echo
say "  Адрес:  $ADDR" "  Open:      $ADDR"
say "  Логин:  $USERNAME" "  Login:     $USERNAME"
[[ $GENERATED -eq 1 ]] && say "  Пароль: $PASSWORD" "  Password:  $PASSWORD"
echo
if [[ $PUBLIC -eq 1 ]]; then
  say "  Сертификат самоподписанный — браузер один раз предупредит. Это ожидаемо:
  шифрование работает, просто подписан он сам себе. Для своего домена есть
  готовый конфиг nginx (nginx.example.conf) и обычный Let's Encrypt." \
      "  The certificate is self-signed, so the browser warns once. That is
  expected: traffic is encrypted, the certificate just vouches for itself.
  For a real domain there is nginx.example.conf and ordinary Let's Encrypt."
  echo
fi
say "  Дальше: откройте адрес, войдите и добавьте свои сайты в разделе Settings.
  Что на сервере уже есть, подскажет:  $PYTHON $DIR/tools/discover.py" \
    "  Next: open the address, sign in, add your sites under Settings.
  To see what this server already runs:  $PYTHON $DIR/tools/discover.py"
echo
