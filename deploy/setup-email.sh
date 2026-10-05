#!/usr/bin/env bash
# Email for leads: a mailbox (Yandex, Gmail or Mail.ru) sends every lead of a site to the manager.
# Run as root:  ai-email-setup [site]
set -euo pipefail
source "${APP:-/opt/site-ai-consultant}/deploy/lib.sh"
cd "$APP"
SITE=${1:-zeleny-kontur}
[ -f "sites/$SITE/site.json" ] || { echo "No site '$SITE' in $APP/sites"; exit 1; }

echo
echo "=== Email for leads: $SITE ==="
read -rp "1) Mailbox that sends the leads (best a separate one, e.g. leads.bot@yandex.ru): " MAILBOX
MAILBOX=$(echo "$MAILBOX" | tr -d '[:space:]')
case "$MAILBOX" in *@*) ;; *) MAILBOX="$MAILBOX@yandex.ru";; esac
case "${MAILBOX##*@}" in
  yandex.ru|ya.ru|yandex.com|yandex.by|yandex.kz|narod.ru)
    HOST=smtp.yandex.ru
    echo "   Yandex, once per mailbox:"
    echo "   a) mail.yandex.ru → Settings → «Почтовые программы»: turn on «С сервера imap.yandex.ru по протоколу IMAP»"
    echo "      and «Пароли приложений и OAuth-токены»;"
    echo "   b) id.yandex.ru/security/app-passwords → «Почта» → copy the password (it is shown only once).";;
  gmail.com|googlemail.com)
    HOST=smtp.gmail.com
    echo "   Gmail: 2-Step Verification must be on; then myaccount.google.com/apppasswords → create → copy the 16 letters.";;
  mail.ru|inbox.ru|list.ru|bk.ru|internet.ru)
    HOST=smtp.mail.ru
    echo "   Mail.ru: account.mail.ru/user/2-step-auth/passwords → «Добавить» → copy the password.";;
  *)
    read -rp "   SMTP server of ${MAILBOX##*@} (Enter = smtp.yandex.ru, for Yandex 360 domains): " HOST
    HOST=${HOST:-smtp.yandex.ru};;
esac
read -rsp "2) App password (hidden while you type — paste and press Enter): " PASS; echo
PASS=$(echo "$PASS" | tr -d '[:space:]')
[ -n "$PASS" ] || { echo "Empty password — run ai-email-setup again."; exit 1; }
read -rp "3) Where to send leads (Enter = $MAILBOX; several: a@x.ru,b@y.ru): " TO
TO_JSON=$(TO="${TO:-$MAILBOX}" python3 -c \
  'import json, os; print(json.dumps([a.strip() for a in os.environ["TO"].split(",") if "@" in a]))')

set_env SMTP_HOST "$HOST"
set_env SMTP_PORT 465
set_env SMTP_USER "$MAILBOX"
set_env SMTP_PASSWORD "$PASS"
set_env SMTP_FROM "AI-консультант <$MAILBOX>"
site_local "$SITE" lead_emails "$TO_JSON"

echo "Restarting..."
docker compose up -d --force-recreate app >/dev/null
wait_healthy >/dev/null || true
echo
OUT=$(docker compose exec -T app python -m app.notify test "$SITE" </dev/null 2>&1 | tee /dev/stderr) || true
echo
case "$OUT" in
  *"email     OK"*)
    echo "Done: a test email went to $TO_JSON (look in Spam too). Every new lead of '$SITE' will arrive there.";;
  *SMTPAuthenticationError*)
    echo "The mailbox refused the login: the app password is wrong, or (Yandex) the «Почтовые программы»"
    echo "switches are off. Fix it and run ai-email-setup again.";;
  *"email     FAILED"*)
    echo "Email failed (see the reason above). Run ai-email-setup again after fixing it.";;
esac
