#!/usr/bin/env bash
# Fixes "Session expired" right after n8n login. Cause found: the login and the next request reach two
# different n8n backends (or the user row is disabled). Safe to run more than once:  sudo bash deploy/fix-n8n-login.sh
set -uo pipefail
cd "${APP:-/opt/site-ai-consultant}"
MAIN=$(docker compose ps -q n8n)
echo "== n8n backends"
docker ps -a --filter ancestor=n8nio/n8n:2.41.4 --format '{{.ID}}  {{.Names}}  {{.Status}}'
docker ps -a --format '{{.ID}} {{.Image}} {{.Names}}' | grep -i n8n | grep -v "^${MAIN:0:12}" | while read -r id image name; do
  echo "removing extra n8n container: $name ($image)"; docker rm -f "$id" >/dev/null
done
ss -ltnp 2>/dev/null | grep -E ':5678\b' | grep -v docker-proxy && echo "^ something on the host listens on 5678 — tell Claude"
echo "Caddy sees n8n at: $(docker compose exec -T caddy nslookup n8n 2>/dev/null </dev/null | awk '/^Address/ && !/#/ {print $2}' | xargs)"
echo "== n8n user"
VOL=$(docker volume inspect site-ai-consultant_n8n_data -f '{{.Mountpoint}}')
python3 deploy/n8ndb.py "$VOL" | sed -n '1,4p'
if python3 deploy/n8ndb.py "$VOL" | grep -q "disabled=1"; then
  echo "user is disabled — enabling"; docker compose stop n8n >/dev/null
  python3 -c "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]+'/database.sqlite'); db.execute('update user set disabled=0'); db.execute('delete from invalid_auth_token'); db.commit()" "$VOL"
  docker compose start n8n >/dev/null
fi
docker compose restart caddy >/dev/null
echo "✅ Done — sign in to n8n again"
