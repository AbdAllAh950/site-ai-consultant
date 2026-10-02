"""Delivering a lead: a Telegram message to the manager and, optionally, a JSON webhook (CRM, n8n, Albato…)."""
from __future__ import annotations

import html

import httpx


def telegram_text(site_name: str, lead: dict, card: dict, history: list[dict]) -> str:
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


class Notifier:
    def __init__(self, token: str, api: str = "https://api.telegram.org", client: httpx.AsyncClient | None = None):
        self.token = token
        self.api = api.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=15)

    async def telegram(self, chat_id: str, text: str) -> bool:
        if not (self.token and chat_id):
            return False
        r = await self.client.post(f"{self.api}/bot{self.token}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        r.raise_for_status()
        return True

    async def webhook(self, url: str, payload: dict) -> bool:
        if not url:
            return False
        r = await self.client.post(url, json=payload)
        r.raise_for_status()
        return True
