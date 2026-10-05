#!/usr/bin/env bash
# Quick health report for screenshots:  sudo bash /opt/site-ai-consultant/deploy/diag.sh
cd "${APP:-/opt/site-ai-consultant}"
echo "== $(date -Is) · $(git log -1 --format='%h %s' | cut -c1-60)"
free -h | sed -n 1,3p
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
echo "n8n restarts: $(docker inspect -f '{{.RestartCount}} (started {{.State.StartedAt}})' "$(docker compose ps -q n8n)" 2>/dev/null)"
echo "== n8n log (last 30 min, important lines)"
docker compose logs n8n --since 30m --no-log-prefix 2>&1 </dev/null \
  | grep -viE "deprecat|-> The default|future version|^\s*-|license SDK|Strapi|community" | tail -25
docker compose logs n8n --since 30m --no-log-prefix 2>&1 </dev/null | grep -iE "browserid|jwt|auth|cookie|unauthor" | tail -5
echo "== autodeploy"
journalctl -u site-ai-autodeploy --since "30 min ago" --no-pager 2>/dev/null | grep -vE "Starting|Finished|Succeeded|Deactivated" | tail -5
echo "== n8n sign-in requests (Caddy access log)"
docker compose exec -T caddy cat /data/n8n-access.log </dev/null 2>/dev/null | python3 deploy/caddylog.py
