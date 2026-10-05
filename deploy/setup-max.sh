#!/usr/bin/env bash
# MAX for leads: connects the client's own MAX bot, so leads of a site also go to their managers in MAX.
# (MAX gives bots only to verified companies, ИП and самозанятые — so the bot belongs to the client.)
# Run as root:  ai-max-setup <site>              — finds the chat by itself (add the bot to a group or press Start)
#               ai-max-setup <site> <chat_id>    — if the chat id is already known
set -euo pipefail
source "${APP:-/opt/site-ai-consultant}/deploy/lib.sh"
cd "$APP"
SITE=${1:?usage: ai-max-setup <site> [chat_id]}
[ -f "sites/$SITE/site.json" ] || { echo "No site '$SITE' in $APP/sites"; exit 1; }
json() { V="$1" python3 -c 'import json, os; print(json.dumps(os.environ["V"]))'; }

echo
echo "=== MAX for leads: $SITE ==="
echo "The client's bot token: MAX partner platform → «Чат-боты» → the bot → ⋮ → «Настройки» → copy the token"
echo "(or the «MAX для бизнеса» mini-app → «Получить токен»)."
read -rsp "Bot token (hidden while you type — paste and press Enter): " TOKEN; echo
TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')
[ -n "$TOKEN" ] || { echo "Empty token — run ai-max-setup again."; exit 1; }
site_local "$SITE" max_token "$(json "$TOKEN")"

if [ -n "${2:-}" ]; then
  KIND=max_chat_id; ID=$2
else
  echo
  # the output goes to the screen as it comes (stderr) and is also kept to read the result line
  OUT=$(docker compose exec -T app python -m app.notify max-connect "$SITE" </dev/null 2>&1 | tee /dev/stderr) || true
  LINE=$(printf '%s\n' "$OUT" | grep '^RESULT ' | tail -1 || true)
  [ -n "$LINE" ] || { echo; echo "MAX is not connected yet — see the message above."; exit 1; }
  PAIR=${LINE#RESULT }; KIND=${PAIR%%=*}; ID=${PAIR#*=}
fi
# exactly one target: a group chat, or one person
site_local "$SITE" max_chat_id '""'
site_local "$SITE" max_user_id '""'
site_local "$SITE" "$KIND" "$(json "$ID")"

echo
docker compose exec -T app python -m app.notify test "$SITE" </dev/null
echo
echo "If max says OK — the test message is in MAX, and new leads of '$SITE' will arrive there."
