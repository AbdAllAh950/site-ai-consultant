#!/usr/bin/env bash
# Deploys the latest main from GitHub: pull, rebuild what changed, restart.
# Runs every minute from site-ai-autodeploy.timer (does nothing if main didn't change).
# Deploy right now:  sudo ai-deploy        Logs:  journalctl -u site-ai-autodeploy -n 50
# The whole script is one function, so `git reset` replacing this file mid-run is harmless.
main() {
  set -euo pipefail
  source "${APP:-/opt/site-ai-consultant}/deploy/lib.sh"
  cd "$APP"
  git fetch -q origin main
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ] || [ "${1:-}" = "--force" ]; then
    local caddy_before; caddy_before=$(sha1sum deploy/Caddyfile)
    git reset -q --hard origin/main
    set_env APP_VERSION "$(git rev-parse --short HEAD)"
    docker compose up -d --build --remove-orphans app caddy
    # Caddyfile is a single-file mount: git replaces the file, so caddy needs a restart to see the new one
    if [ "${1:-}" = "--force" ] || [ "$caddy_before" != "$(sha1sum deploy/Caddyfile)" ]; then
      docker compose restart caddy >/dev/null
    fi
    # n8n is optional: if its image can't be pulled right now, the consultant still deploys
    docker compose up -d n8n || echo "n8n not started (image pull failed?) — will retry on the next deploy"
    echo "$(date -Is) deployed $(get_env APP_VERSION)"
  fi
  commands
  n8n_import
}

# Server commands (ai-deploy, ai-email-setup, ai-max-setup…) — new ones appear without re-running bootstrap.
commands() {
  local bin=/usr/local/bin
  ln -sf "$APP/deploy/autodeploy.sh" $bin/ai-deploy
  ln -sf "$APP/deploy/ai-setup.sh" $bin/ai-setup
  ln -sf "$APP/deploy/setup-email.sh" $bin/ai-email-setup
  ln -sf "$APP/deploy/setup-max.sh" $bin/ai-max-setup
}

# Imports n8n/*.json once n8n is running (retried every minute until it works).
n8n_import() {
  [ -f "$APP/.n8n-imported" ] && return 0
  [ -n "$(docker compose ps -q --status running n8n 2>/dev/null)" ] || return 0
  local f
  for f in "$APP"/n8n/*.json; do
    docker compose exec -T n8n n8n import:workflow --input="/workflows/$(basename "$f")" </dev/null >/dev/null 2>&1 || return 0
  done
  touch "$APP/.n8n-imported"
  echo "$(date -Is) n8n workflows imported"
}

main "$@"; exit $?
