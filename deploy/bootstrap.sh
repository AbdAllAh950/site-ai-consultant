#!/usr/bin/env bash
# One-time switch of the demo server to auto-deploy from GitHub (safe to run again). As root:
#   git -C /opt/site-ai-consultant fetch origin main && git -C /opt/site-ai-consultant show origin/main:deploy/bootstrap.sh > /tmp/ai-bootstrap.sh && bash /tmp/ai-bootstrap.sh
set -euo pipefail
APP=${APP:-/opt/site-ai-consultant}
UNITS=${UNITS:-/etc/systemd/system}
BIN=${BIN:-/usr/local/bin}
cd "$APP"
echo "== 1/5 Private settings out of git =="
python3 - <<'PY'
import json, pathlib
for cfg in pathlib.Path("sites").glob("*/site.json"):
    d = json.loads(cfg.read_text(encoding="utf-8"))
    private = {k: d[k] for k in ("telegram_chat_id", "webhook_url", "lead_emails", "max_chat_id", "max_user_id", "max_token") if d.get(k)}
    if private:
        local = cfg.with_name("site.local.json")
        cur = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
        local.write_text(json.dumps({**private, **cur}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  saved", ", ".join(private), "->", local)
PY
echo "== 2/5 Latest code =="
git fetch -q origin main
git reset -q --hard origin/main
source "$APP/deploy/lib.sh"
CHAT=$(python3 -c 'import json; print(json.load(open("sites/zeleny-kontur/site.local.json")).get("telegram_chat_id",""))' 2>/dev/null || true)
[ -n "$CHAT" ] && set_env LEADS_TELEGRAM_CHAT_ID "$CHAT"
site_local zeleny-kontur webhook_url '"http://n8n:5678/webhook/ai-lead"'
echo "  $(git log -1 --format='%h %s')"

echo "== 3/5 Memory for n8n =="
if [ -z "${SKIP_SWAP:-}" ] && [ "$(swapon --show | wc -l)" -eq 0 ] && [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 3500000 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "  added 2 GB swap"
fi
free -h | sed -n 1,3p

echo "== 4/5 Auto-deploy every minute + commands =="
cat > $UNITS/site-ai-autodeploy.service <<UNIT
[Unit]
Description=Deploy site-ai-consultant from GitHub if main changed
After=docker.service network-online.target
[Service]
Type=oneshot
Environment=HOME=/root
ExecStart=$APP/deploy/autodeploy.sh
UNIT
cat > $UNITS/site-ai-autodeploy.timer <<UNIT
[Unit]
Description=Check GitHub for site-ai-consultant updates every minute
[Timer]
OnActiveSec=20s
OnUnitInactiveSec=1min
[Install]
WantedBy=timers.target
UNIT
ln -sf "$APP/deploy/autodeploy.sh" $BIN/ai-deploy
ln -sf "$APP/deploy/ai-setup.sh" $BIN/ai-setup
ln -sf "$APP/deploy/setup-email.sh" $BIN/ai-email-setup
systemctl daemon-reload

echo "== 5/5 Build and start (2–5 minutes the first time) =="
"$APP/deploy/autodeploy.sh" --force || echo "deploy step failed — see the lines above"
systemctl enable site-ai-autodeploy.timer >/dev/null
systemctl restart site-ai-autodeploy.timer
echo
if wait_healthy >/dev/null; then echo "✅ Done: $(curl -fsS "https://$(get_env DOMAIN)/health")"; else echo "❌ The app did not answer — send me a screenshot"; fi
systemctl list-timers site-ai-autodeploy.timer --no-pager | head -2
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
