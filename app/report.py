"""Weekly report for a client: dialogs, leads, conversion and what visitors ask first.

python -m app.report zeleny-kontur            # print
python -m app.report zeleny-kontur --send     # also send to the site's Telegram chat
"""
from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from .config import Settings, SiteRegistry
from .notify import Notifier
from .store import Store


def render(site_name: str, st: dict) -> str:
    lines = [f"📊 AI-консультант · {site_name} · последние {st['days']} дн.",
             f"Диалогов: {st['dialogs']} · сообщений от посетителей: {st['messages']}",
             f"Заявок: {st['leads']} · конверсия диалога в заявку: {st['conversion_pct']}%"]
    if st["first_questions"]:
        lines.append("С чего начинали разговор:")
        lines += [f"— {q[:120]}" for q in st["first_questions"]]
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Weekly AI consultant report")
    ap.add_argument("site")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--send", action="store_true", help="send to the site's Telegram chat")
    args = ap.parse_args()
    s = Settings.from_env()
    site = SiteRegistry(s.sites_dir).get(args.site)
    if not site:
        raise SystemExit(f"unknown site: {args.site}")
    text = render(site.name, Store(s.db_path).stats(site.id, args.days))
    print(text)
    if args.send:
        import html
        asyncio.run(Notifier(s.telegram_token, s.telegram_api).telegram(site.telegram_chat_id, html.escape(text)))


if __name__ == "__main__":
    main()
