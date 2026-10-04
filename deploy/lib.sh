# Shared helpers for the server scripts (sourced, not run).
APP=${APP:-/opt/site-ai-consultant}

# set_env KEY VALUE — replace or add a line in .env, keeping the file private
set_env() {
  touch "$APP/.env"; chmod 600 "$APP/.env"
  grep -v "^$1=" "$APP/.env" > "$APP/.env.tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$APP/.env.tmp"
  mv "$APP/.env.tmp" "$APP/.env"; chmod 600 "$APP/.env"
}

get_env() { grep -E "^$1=" "$APP/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }

# site_local SITE KEY JSON_VALUE — set a private per-site setting in sites/SITE/site.local.json (not in git)
site_local() {
  python3 - "$APP/sites/$1/site.local.json" "$2" "$3" <<'PY'
import json, os, sys
path, key, value = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
data[key] = value
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
}

wait_healthy() {
  local domain; domain=$(get_env DOMAIN)
  for _ in $(seq 1 45); do
    curl -fsS "https://$domain/health" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}
