#!/usr/bin/env bash
# Connects YandexGPT and the Telegram bot to the AI consultant on this server. Run as root:  ai-setup
# Telegram is blocked from many Russian servers: set TG_API to a relay, e.g.
#   TG_API='https://script.google.com/macros/s/<id>/exec?p=' ai-setup
set -euo pipefail
source "${APP:-/opt/site-ai-consultant}/deploy/lib.sh"
cd "$APP"
SITE=${SITE:-zeleny-kontur}
TG_API=${TG_API:-$(get_env TELEGRAM_API_URL)}
TG_API=${TG_API:-https://api.telegram.org}
DOMAIN=$(get_env DOMAIN)
echo
echo "=== YandexGPT and Telegram ==="
echo "Keys are hidden while you type — paste and press Enter."
echo
read -rp  "1) Yandex Cloud folder id: " FOLDER
read -rsp "2) Yandex Cloud API key: " KEY; echo
read -rsp "3) Telegram bot token: " TOKEN; echo
FOLDER=$(echo "$FOLDER" | tr -d '[:space:]'); KEY=$(echo "$KEY" | tr -d '[:space:]'); TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')
[ -n "$FOLDER" ] && [ -n "$KEY" ] && [ -n "$TOKEN" ] || { echo "Something is empty — run ai-setup again."; exit 1; }

BOT=$(curl -m 30 -fsSL "${TG_API}/bot${TOKEN}/getMe" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["username"])') \
  || { echo "Telegram did not accept the token (or is unreachable: set TG_API)."; exit 1; }
echo "Bot: @$BOT"
echo "4) Open @$BOT in Telegram, press Start (/start), come back and press Enter."
read -r _
CHAT=$(curl -m 30 -fsSL "${TG_API}/bot${TOKEN}/getUpdates" | python3 -c '
import sys, json
ids = [u[k]["chat"]["id"] for u in json.load(sys.stdin).get("result", []) for k in ("message", "my_chat_member") if k in u]
print(ids[-1] if ids else "")')
[ -n "$CHAT" ] || read -rp "No /start seen. Enter the chat id (from @userinfobot): " CHAT

set_env LLM_URL https://ai.api.cloud.yandex.net/v1/chat/completions
set_env LLM_AUTH "Api-Key $KEY"
set_env LLM_PROJECT "$FOLDER"
[ -n "$(get_env LLM_MODEL)" ] || set_env LLM_MODEL "gpt://$FOLDER/aliceai-llm"
set_env LLM_SUMMARY_MODEL "gpt://$FOLDER/yandexgpt-lite/latest"
set_env TELEGRAM_BOT_TOKEN "$TOKEN"
set_env TELEGRAM_API_URL "$TG_API"
set_env LEADS_TELEGRAM_CHAT_ID "$CHAT"
site_local "$SITE" telegram_chat_id "\"$CHAT\""

echo "Restarting..."
docker compose up -d --force-recreate app n8n >/dev/null 2>&1 || docker compose up -d --force-recreate app >/dev/null
wait_healthy >/dev/null || true
BY=$(curl -sS -X POST "https://$DOMAIN/api/chat" -H 'Content-Type: application/json' \
  -d '{"site":"'$SITE'","session":"setup-check-'$RANDOM$RANDOM'","message":"Сколько стоит газон?","lang":"ru"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("by",""))' 2>/dev/null || true)
echo
[ "$BY" = "ai" ] && echo "YandexGPT: ✅ answers" || echo "YandexGPT: ❌ no answer ($BY) — check the key, folder id and role ai.languageModels.user"
docker compose exec -T app python -m app.notify test "$SITE"
echo "Demo: https://$DOMAIN/demo/"
