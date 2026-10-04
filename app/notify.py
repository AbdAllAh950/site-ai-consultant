"""Delivering a lead to the manager: Telegram, MAX, email and, optionally, a JSON webhook (CRM, n8n, Albato…).

python -m app.notify test <site>     # sends a test message to every channel configured for the site
"""
from __future__ import annotations

import asyncio
import html
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

import httpx


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
                 send_mail: Callable[[Smtp, EmailMessage], None] = smtp_send):
        self.token = token
        self.api = api.rstrip("/")
        # the Telegram relay (Apps Script) answers with a redirect to the result
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        self.max_token = max_token
        self.max_api = max_api.rstrip("/")
        self.smtp = smtp or Smtp("", 465, "", "")
        self.send_mail = send_mail

    async def telegram(self, chat_id: str, text: str) -> bool:
        if not (self.token and chat_id):
            return False
        r = await self.client.post(f"{self.api}/bot{self.token}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        r.raise_for_status()
        return True

    async def max(self, text: str, chat_id: str = "", user_id: str = "", token: str = "") -> bool:
        """MAX Bot API: POST /messages?chat_id=… (a group) or ?user_id=… (one person), token in Authorization."""
        token = token or self.max_token
        if not token or not (chat_id or user_id):
            return False
        params = {"chat_id": chat_id} if chat_id else {"user_id": user_id}
        r = await self.client.post(f"{self.max_api}/messages", params={**params, "disable_link_preview": "true"},
                                   headers={"Authorization": token},
                                   json={"text": text[:4000], "format": "html"})
        r.raise_for_status()
        return True

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
    ap = argparse.ArgumentParser(description="Send a test message to every lead channel of a site")
    ap.add_argument("command", choices=["test"])
    ap.add_argument("site")
    args = ap.parse_args()
    s = Settings.from_env()
    site = SiteRegistry(s.sites_dir).get(args.site)
    if not site:
        raise SystemExit(f"unknown site: {args.site}")
    n = from_settings(s)
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
