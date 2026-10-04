#!/usr/bin/env bash
# Email for leads: a Yandex mailbox sends every lead to the manager. Run as root:  ai-email-setup [site]
set -euo pipefail
source "${APP:-/opt/site-ai-consultant}/deploy/lib.sh"
cd "$APP"
SITE=${1:-zeleny-kontur}
echo
echo "=== Email for leads (Yandex Mail) ==="
echo "Use an app password from passport.yandex.ru/security/app-passwords (not your normal password)."
echo
read -rp  "1) Yandex address that sends the leads (name@yandex.ru): " MAILBOX
read -rsp "2) App password (hidden while you type — paste and press Enter): " PASS; echo
read -rp  "3) Where to send leads (Enter = the same address): " TO
MAILBOX=$(echo "$MAILBOX" | tr -d '[:space:]'); PASS=$(echo "$PASS" | tr -d '[:space:]'); TO=$(echo "${TO:-$MAILBOX}" | tr -d '[:space:]')
case "$MAILBOX" in *@*) ;; *) MAILBOX="$MAILBOX@yandex.ru";; esac
[ -n "$PASS" ] || { echo "Empty password — run ai-email-setup again."; exit 1; }

set_env SMTP_HOST smtp.yandex.ru
set_env SMTP_PORT 465
set_env SMTP_USER "$MAILBOX"
set_env SMTP_PASSWORD "$PASS"
set_env SMTP_FROM "AI-консультант <$MAILBOX>"
site_local "$SITE" lead_emails "[\"$TO\"]"

echo "Restarting..."
docker compose up -d --force-recreate app >/dev/null
wait_healthy >/dev/null || true
echo
docker compose exec -T app python -m app.notify test "$SITE"
echo
echo "If email says OK — check $TO (and the Spam folder)."
