"""Delivering a lead to the manager: Telegram, MAX, email and, optionally, a JSON webhook (CRM, n8n, Albato…).

python -m app.notify test <site>          # sends a test message to every channel configured for the site
python -m app.notify max-connect <site>   # finds the MAX chat of the site's bot (used by ai-max-setup)
"""
from __future__ import annotations

import asyncio
import html
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

import certifi
import httpx

# MAX's API (platform-api2.max.ru) is signed by the Russian Trusted CA (Минцифры), which isn't in certifi
RU_CA = Path(__file__).parent / "certs" / "russian_trusted_ca.pem"


def max_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(RU_CA))
    return ctx


def telegram_text(site_name: str, lead: dict, card: dict, history: list[dict]) -> str:
    """The lead as a short HTML message. MAX accepts the same tags (<b>, <i>, <code>)."""
    e = html.escape
    lines = [f"🟢 <b>Заявка из AI-чата</b> · {e(site_name)}",
             f"👤 {e(lead['name'])} · <code>{e(lead['phone'])}</code>"]
    for icon, key, label in (("🧩", "service", "Что нужно"), ("📐", "details", "Детали"),
                             ("💰", "budget", "Бюджет"), ("⏱", "timeline", "Сроки")):
        if card.get(key):
            lines.append(f"{icon} {label}: {e(card[key])}")
    if card.get("summary"):
        lines.append(f"📝 {e(card['summary'])}")
    if lead.get("comment"):
        lines.append(f"✍️ Комментарий: {e(lead['comment'])}")
    asked = [m["content"] for m in history if m["role"] == "user"][-3:]
    if asked:
        lines.append("💬 Спрашивал(а):\n" + "\n".join(f"— <i>{e(q[:200])}</i>" for q in asked))
    lines.append(f"🌐 {e(lead.get('page') or 'сайт')} · {lead['lang']}")
    return "\n".join(lines)


def plain(text_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text_html))


def email_html(text_html: str) -> str:
    body = text_html.replace("\n", "<br>\n")
    return ('<div style="font:15px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#1d2b24;max-width:560px">'
            f"{body}</div>")


@dataclass(frozen=True)
class Smtp:
    host: str
    port: int
    user: str
    password: str
    sender: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.user and self.password)


def smtp_send(cfg: Smtp, msg: EmailMessage) -> None:
    """465 → SSL from the start; 587/25 → STARTTLS."""
    ctx = ssl.create_default_context()
    if cfg.port == 465:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=20, context=ctx) as s:
            s.login(cfg.user, cfg.password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(cfg.user, cfg.password)
            s.send_message(msg)


class Notifier:
    def __init__(self, token: str, api: str = "https://api.telegram.org", client: httpx.AsyncClient | None = None,
                 *, max_token: str = "", max_api: str = "https://platform-api2.max.ru", smtp: Smtp | None = None,
                 send_mail: Callable[[Smtp, EmailMessage], None] = smtp_send,
                 max_client: httpx.AsyncClient | None = None):
        self.token = token
        self.api = api.rstrip("/")
        # the Telegram relay (Apps Script) answers with a redirect to the result
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        # MAX gets its own client that also trusts the Russian CA (an injected client is used as is)
        self.max_client = max_client or (client if client is not None else
                                         httpx.AsyncClient(timeout=40, verify=max_ssl()))
        self.max_token = max_token
        self.max_api = max_api.rstrip("/")
        self.smtp = smtp or Smtp("", 465, "", "")
        self.send_mail = send_mail

    async def telegram(self, chat_id: str, text: str, *, buttons: list[tuple[str, str]] | None = None,
                       silent: bool = False) -> bool:
        """buttons: (label, url) pairs shown under the message; silent: no sound (night alerts)."""
        if not (self.token and chat_id):
            return False
        body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u}] for t, u in buttons]}
        if silent:
            body["disable_notification"] = True
        r = await self.client.post(f"{self.api}/bot{self.token}/sendMessage", json=body)
        r.raise_for_status()
        # the relay answers 200 even when Telegram refuses the message, so read Telegram's own verdict
        try:
            answer = r.json()
        except ValueError:
            answer = {}
        if isinstance(answer, dict) and answer.get("ok") is False:
            raise RuntimeError(f"Telegram refused the message: {str(answer.get('description'))[:200]}")
        return True

    async def max(self, text: str, chat_id: str = "", user_id: str = "", token: str = "") -> bool:
        """MAX Bot API: POST /messages?chat_id=… (a group) or ?user_id=… (one person), token in Authorization."""
        token = token or self.max_token
        if not token or not (chat_id or user_id):
            return False
        params = {"chat_id": chat_id} if chat_id else {"user_id": user_id}
        r = await self.max_client.post(f"{self.max_api}/messages", params={**params, "disable_link_preview": "true"},
                                       headers={"Authorization": token},
                                       json={"text": text[:4000], "format": "html"})
        r.raise_for_status()
        return True

    async def max_get(self, path: str, token: str, **params) -> dict:
        r = await self.max_client.get(f"{self.max_api}{path}", headers={"Authorization": token},
                                      params={k: v for k, v in params.items() if v is not None}, timeout=60)
        r.raise_for_status()
        return r.json()

    async def email(self, to: list[str], subject: str, text_html: str) -> bool:
        to = [a.strip() for a in to if a and "@" in a]
        if not (self.smtp.enabled and to):
            return False
        msg = EmailMessage()
        msg["From"] = self.smtp.sender or self.smtp.user
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(plain(text_html))
        msg.add_alternative(email_html(text_html), subtype="html")
        await asyncio.to_thread(self.send_mail, self.smtp, msg)
        return True

    async def webhook(self, url: str, payload: dict) -> bool:
        if not url:
            return False
        r = await self.client.post(url, json=payload)
        r.raise_for_status()
        return True


def max_target(update: dict) -> tuple[str, str, str] | None:
    """Where leads should go, from one MAX update: ("max_chat_id" | "max_user_id", id, who/what).
    bot_added — the bot joined a group; bot_started — someone pressed Start; message_created — any message."""
    kind = update.get("update_type")
    user = update.get("user") or {}
    name = " ".join(filter(None, [user.get("first_name") or user.get("name"), user.get("last_name")])) or "?"
    if kind == "bot_added" and update.get("chat_id"):
        return "max_chat_id", str(update["chat_id"]), f"group chat (bot added by {name})"
    if kind == "bot_started" and user.get("user_id"):
        return "max_user_id", str(user["user_id"]), f"private chat with {name}"
    if kind == "message_created":
        msg = update.get("message") or {}
        to, sender = msg.get("recipient") or {}, msg.get("sender") or {}
        who = sender.get("first_name") or sender.get("name") or "?"
        if to.get("chat_type") == "dialog" and sender.get("user_id"):
            return "max_user_id", str(sender["user_id"]), f"private chat with {who}"
        if to.get("chat_id"):
            return "max_chat_id", str(to["chat_id"]), f"group chat (message from {who})"
    return None


async def max_connect(n: Notifier, token: str, wait_s: int = 600) -> tuple[str, str, str] | None:
    """Shows the bot, then waits for the manager to add it to a group or press Start, and returns the target."""
    me = await n.max_get("/me", token)
    bot = me.get("first_name") or me.get("name") or "bot"
    print(f"Bot: {bot}" + (f" (@{me['username']})" if me.get("username") else ""))
    print("Now, in MAX, do ONE of these:\n"
          "  • group of managers: add the bot to the group and make it an administrator;\n"
          "  • one manager: open the bot and press «Начать» (Start), or send it any message.\n"
          f"Waiting up to {wait_s // 60} minutes…", flush=True)
    marker = (await n.max_get("/updates", token, timeout=0, limit=1000)).get("marker")   # skip old events
    deadline = asyncio.get_running_loop().time() + wait_s
    while asyncio.get_running_loop().time() < deadline:
        data = await n.max_get("/updates", token, marker=marker, timeout=30, limit=100)
        marker = data.get("marker", marker)
        for update in data.get("updates") or []:
            found = max_target(update)
            if found:
                return found
    return None


def from_settings(s) -> Notifier:
    return Notifier(s.telegram_token, s.telegram_api, max_token=s.max_token, max_api=s.max_api,
                    smtp=Smtp(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from))


def main() -> None:
    import argparse
    import logging

    from dotenv import load_dotenv

    from .config import Settings, SiteRegistry

    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Lead channels of a site: test them, connect MAX")
    ap.add_argument("command", choices=["test", "max-connect"])
    ap.add_argument("site")
    ap.add_argument("--wait", type=int, default=600, help="max-connect: seconds to wait for the bot to be added")
    args = ap.parse_args()
    s = Settings.from_env()
    site = SiteRegistry(s.sites_dir).get(args.site)
    if not site:
        raise SystemExit(f"unknown site: {args.site}")
    n = from_settings(s)
    if args.command == "max-connect":
        token = site.max_token or s.max_token
        if not token:
            raise SystemExit("no MAX bot token for this site (ai-max-setup saves it to site.local.json)")
        try:
            found = asyncio.run(max_connect(n, token, args.wait))
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise SystemExit("MAX rejected the token (401) — copy it again from the bot settings" if code == 401
                             else f"MAX answered {code}: {exc.response.text[:200]}")
        except httpx.HTTPError as exc:
            raise SystemExit(f"cannot reach MAX: {type(exc).__name__}: {str(exc)[:200]}")
        if not found:
            raise SystemExit("No event from MAX. If the bot already has a webhook, MAX doesn't give events here — "
                             "enter the chat id by hand: ai-max-setup <site> <chat_id>")
        kind, target, label = found
        print(f"Found: {label}\nRESULT {kind}={target}")
        return
    text = f"✅ <b>Проверка канала заявок</b> · {html.escape(site.name)}\nЗаявки с AI-чата будут приходить сюда."

    async def run() -> None:
        checks = {
            "telegram": n.telegram(site.telegram_chat_id, text),
            "max": n.max(text, site.max_chat_id, site.max_user_id, site.max_token),
            "email": n.email(site.lead_emails, f"Проверка: заявки AI-чата · {site.name}", text),
        }
        for name, coro in checks.items():
            try:
                print(f"{name:9} {'OK' if await coro else 'not configured'}")
            except Exception as exc:  # show the reason, never the secret
                print(f"{name:9} FAILED: {type(exc).__name__}: {str(exc)[:200]}")
    asyncio.run(run())


if __name__ == "__main__":
    main()
