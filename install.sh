#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")"

case "${1:-}" in
  --update)
    git fetch https://github.com/mahxray/cloudflare-telegram-manager.git main
    git merge --ff-only FETCH_HEAD
    exec bash ./install.sh
    ;;
  "")
    ;;
  *)
    echo 'Usage: bash install.sh [--update]' >&2
    exit 2
    ;;
esac

# Repair and upgrade without changing any existing authentication secrets.
mkdir -p data backups
TS="$(date +%Y%m%d-%H%M%S)"
if [[ -f .env ]]; then
  cp -a .env "backups/env.${TS}"
  chmod 600 "backups/env.${TS}"
fi
if [[ -f data/bot.db ]]; then
  # SQLite native online backup includes transactions in WAL mode.
  python3 - "data/bot.db" "backups/bot.db.${TS}" <<'PYCODE'
import sqlite3, sys
src, target = sys.argv[1:]
with sqlite3.connect(src) as a, sqlite3.connect(target) as b:
    a.backup(b)
print('SQLite backup saved.')
PYCODE
  chmod 600 "backups/bot.db.${TS}"
fi

make_key() {
  python3 - <<'PYCODE'
import base64,secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PYCODE
}

if [[ ! -f .env ]]; then
  read -rsp 'Telegram Bot Token: ' BOT; echo
  read -rp 'Telegram Admin numeric ID(s), comma separated: ' ADM
  KEY="$(make_key)"
  printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_ADMIN_IDS=%s\nENCRYPTION_KEY=%s\nDATA_DIR=/app/data\n' "$BOT" "$ADM" "$KEY" > .env
  chmod 600 .env
else
  echo 'Preserving existing .env and credentials.'
  if ! grep -Eq '^ENCRYPTION_KEY=.+$' .env; then
    OLD_KEY=""
    while IFS= read -r file; do
      key="$(sed -n 's/^ENCRYPTION_KEY=//p' "$file" | tail -n1 | tr -d '\r')"
      if [[ -n "$key" ]]; then OLD_KEY="$key"; break; fi
    done < <(find backups -maxdepth 1 -type f -name 'env.*' -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-)
    if [[ -z "$OLD_KEY" ]]; then
      # If database contains credentials, do not silently replace their encryption key.
      if [[ -f data/bot.db ]] && python3 - 'data/bot.db' <<'PYCODE'
import sqlite3,sys
try:
    c=sqlite3.connect(f'file:{sys.argv[1]}?mode=ro',uri=True)
    tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'cf_accounts' in tables and c.execute('SELECT COUNT(*) FROM cf_accounts').fetchone()[0]>0:
        sys.exit(0)
    if 'accounts' in tables and c.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]>0:
        sys.exit(0)
except sqlite3.Error:
    sys.exit(0)  # Fail closed if credentials cannot be ruled out.
sys.exit(1)
PYCODE
      then
        echo 'ERROR: No ENCRYPTION_KEY found but encrypted account data exists. Restore the original key from an old .env backup first.' >&2
        exit 1
      fi
      OLD_KEY="$(make_key)"
      echo 'No old account credentials detected; created new encryption key.'
    else
      echo 'Reusing encryption key from earlier .env backup.'
    fi
    printf '\nENCRYPTION_KEY=%s\n' "$OLD_KEY" >> .env
  fi
  if ! grep -Eq '^DATA_DIR=.+$' .env; then
    printf '\nDATA_DIR=/app/data\n' >> .env
  fi
  chmod 600 .env
fi

# Validate presence, without ever printing secret values.
for var in TELEGRAM_BOT_TOKEN TELEGRAM_ADMIN_IDS ENCRYPTION_KEY; do
  if ! grep -Eq "^${var}=.+$" .env; then
    echo "ERROR: ${var} missing in .env" >&2
    exit 1
  fi
done

# A bind mount retains HOST ownership; Dockerfile chown /app/data does NOT fix this.
# The container executes as UID 10001, and SQLite must be able to create -wal/-shm.
chown -R 10001:10001 data
chmod 700 data
find data -type d -exec chmod 700 {} +
find data -type f -exec chmod 600 {} +
echo 'Database mount ownership fixed for container UID 10001.'

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker.io docker-compose-plugin || apt-get install -y docker.io docker-compose
  systemctl enable --now docker
fi
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(docker-compose)
fi
"${DC[@]}" config --quiet
"${DC[@]}" build --pull
"${DC[@]}" up -d --remove-orphans --force-recreate
sleep 4
"${DC[@]}" ps

CID="$("${DC[@]}" ps -q cloudflare-bot | head -n1)"
if [[ -n "$CID" ]]; then
  STATUS="$(docker inspect -f '{{.State.Status}}' "$CID" 2>/dev/null || true)"
  if [[ "$STATUS" != 'running' ]]; then
    echo "WARNING: Bot container status is ${STATUS}. Inspect sanitized logs via ./diagnose.sh." >&2
    "${DC[@]}" logs --tail=20 | sed -E 's/[A-Za-z0-9_-]{36,}/[REDACTED]/g' || true
    exit 1
  fi
fi
echo 'Installed/updated Cloudflare Telegram Manager.'
echo 'Open your bot in Telegram, send /start, and choose Quick Cloudflare setup to connect an account.'
echo 'Run ./diagnose.sh if Telegram commands do not respond.'
