"""FL.ru watcher: new projects that fit the owner's profile go to Telegram with a ready Russian reply.

On FL.ru the first specific reply wins: a fitting bot project collects 40+ replies within three hours.
Every few minutes this reads FL.ru's public RSS for the chosen categories, keeps fresh projects that
match the keywords, reads the full description on the project page, asks the model to score the fit
(0–10) and to draft a reply, and sends the good ones to Telegram through the same bot and relay as the
leads. Seen projects are stored in SQLite, so restarts and deploys never repeat an alert. The owner
reads the draft, edits it if needed and sends it on FL.ru himself.

Runs inside the app (FL_WATCH=0 turns it off). Alerts go to FL_TELEGRAM_CHAT_ID, else to
LEADS_TELEGRAM_CHAT_ID, else to the demo site's Telegram chat.

python -m app.flwatch check     # one pass: prints what would be sent, with drafts; sends nothing
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .config import ROOT, Settings, SiteRegistry
from .consultant import Consultant
from .notify import Notifier

log = logging.getLogger("flwatch")
FL = "https://www.fl.ru"
MSK = timezone(timedelta(hours=3))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
DEMO_SITE = "zeleny-kontur"          # its Telegram chat is the owner's own
DEMO_URL = "https://111-88-156-53.sslip.io/demo/"
MAX_AGE = timedelta(hours=8)         # older projects already have dozens of replies
MIN_BUDGET = 2000                    # ₽: smaller fixed budgets are not worth a paid reply
WARN_AFTER = 5                       # failed polls in a row before a warning (5 × 3 min = 15 min)
STATUS: dict = {"enabled": False}    # shown in /health

WANT = re.compile(
    r"бот|chat ?bot|\bии\b|\bai\b|нейросет|gpt|llm|\brag\b|баз\w* знаний|ассистент|агент|n8n|make\.com|albato|"
    r"автоматиз|интеграц|amo ?crm|амо ?срм|битрикс|bitrix|\bcrm\b|срм|tilda|тильд|webhook|вебхук|\bapi\b|"
    r"парс|telegram|телеграм|\bmax\b|\bмакс\b|whatsapp|ватсап|android|андро[иийі]д|\bios\b|app ?store|"
    r"google play|rustore|kotlin|котлин|flutter|react native|python|fastapi|анализ данных|дашборд|"
    r"google (sheets|таблиц)|локализац|арабск", re.I)
STRONG = re.compile(r"бот|\bии\b|\bai\b|gpt|llm|\brag\b|агент|n8n|автоматиз|интеграц|crm|срм|tilda|тильд|"
                    r"app ?store|google play|арабск", re.I)
SKIP = re.compile(
    r"в офис|в штат|видеомонтаж|озвучк|логотип|\bseo\b|накрут|казино|букмекер|ставки на спорт|18\+|nsfw|"
    r"криптовалют|\b1[сc]\b|бпла|курсов\w* работ|диплом|реферат|контрольн|экзамен", re.I)
SKIP_CATEGORY = re.compile(
    r"Генерация (видео|изображений|голоса|музыки)|Midjourney|Stable Diffusion|Kandinsky|DALL-E|Firefly|"
    r"AI-аватары|^Аудио|^Дизайн /|^3D|^Игры|SEO|^Реклама|^Тексты|^Маркетплейс", re.I)
SUFFIX = re.compile(r"\s*\((Бюджет:[^()]*|для всех)\)\s*$")
DESCRIPTION = re.compile(r'id="projectp\d+"[^>]*>(.*?)</div>', re.S)

PROMPT = """Ты помогаешь фрилансеру отбирать заказы на FL.ru и писать на них отклики.

О фрилансере — только эти факты, ничего не добавляй:
{profile}

Оцени заказ и напиши отклик от его имени. Верни только JSON:
{{"fit": 0, "why": "", "price": "", "days": "", "reply": ""}}

fit — насколько заказ ему подходит, 0–10. 9–10: точно его профиль, реально сделать одному за 1–3 недели \
по вечерам. 6–8: подходит с оговорками. 0–5: не его профиль; вакансия в штат или офис; нужен опыт, которого \
у него нет; серые схемы, обход правил площадок или законов; оплата ниже ~800 ₽ в час; нужно личное присутствие.
why — одна короткая фраза на арабском языке: почему подходит или нет.
price — сумма в рублях одним числом для поля «Стоимость»: бюджет заказчика, если он указан и адекватен, иначе \
рыночная оценка. days — срок в днях одним числом.
reply — отклик на русском, 500–1000 знаков, обычным текстом:
1) «Здравствуйте!» (с именем, если оно есть в тексте) и одна фраза, что именно сделаешь — словами заказчика;
2) 2–4 пункта: как конкретно решишь задачу — шаги, подводные камни, как проверишь результат;
3) цена и срок одной строкой;
4) один конкретный вопрос по задаче.
Если заказчик просит в отклике ответить на вопросы — ответь на каждый. Ссылку на демо {demo} добавляй только \
к заказам про чат-ботов, AI-консультантов и обработку заявок. Нельзя: выдумывать опыт, кейсы, отзывы и годы \
стажа; писать «я опытный специалист»; обещать то, что зависит от чужих сервисов; markdown, эмодзи, заголовки.

Пример хорошего отклика (заказ: доработать Telegram-бот-парсер — дубликаты, фильтр ИИ, парсер Copart, 9 000 ₽):
Здравствуйте! Возьмусь за все три задачи в рамках 9 000 ₽.
1. Дубликаты: добавлю проверку по ID сообщения и по хешу текста — одно объявление часто приходит из разных \
чатов. Каждое сообщение будет приходить один раз.
2. Фильтр ИИ: перепишу промпт на строгий ответ «подходит / не подходит + причина» и добавлю примеры из ваших \
сообщений, которые сейчас проходят зря. Прогоню на 50–100 старых сообщениях и покажу, сколько отсеивается.
3. Copart → Telegram: парсер по вашим фильтрам. У Copart есть защита от автоматического сбора, поэтому в первый \
день проверю рабочий способ и сразу скажу вам.
Дубликаты и промпт — 1–2 дня, Copart — ещё 2–3 дня. Подскажите, на чём написан бот (Python или Node.js) и где \
он запущен?"""


@dataclass
class Project:
    id: str
    title: str
    url: str
    category: str
    published: datetime
    summary: str = ""
    budget: str = ""                 # as FL.ru shows it, e.g. "9 000 ₽"
    budget_rub: int | None = None
    for_all: bool = False            # "для всех": any freelancer may reply
    description: str = ""            # full text from the project page


@dataclass
class Verdict:
    fit: int
    why: str
    price: str
    days: str
    reply: str


def text_of(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = "\n".join(" ".join(line.split()) for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_rss(data: bytes | str) -> list[Project]:
    root = ET.fromstring(data.encode() if isinstance(data, str) else data)
    projects = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        found = re.search(r"/projects/(\d+)", link)
        if not found:
            continue
        title = " ".join(html.unescape(item.findtext("title") or "").split())
        budget, for_all = "", False
        suffix = SUFFIX.search(title)
        if suffix:
            inner, title = suffix.group(1), title[:suffix.start()].strip()
            for_all = "для всех" in inner
            amount = re.search(r"Бюджет:\s*([^,]+)", inner)
            budget = amount.group(1).strip() if amount else ""
        digits = re.sub(r"\D", "", budget)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        projects.append(Project(
            id=found.group(1), title=title, url=link, category=html.unescape(item.findtext("category") or "").strip(),
            published=published, summary=text_of(item.findtext("description") or ""), budget=budget,
            budget_rub=int(digits) if digits and "₽" in budget else None, for_all=for_all))
    return projects


def page_description(page: str) -> str:
    found = DESCRIPTION.search(page)
    return text_of(found.group(1)) if found else ""


def relevant(p: Project) -> bool:
    text = f"{p.title}\n{p.summary}"
    if SKIP.search(text):
        return False
    if SKIP_CATEGORY.search(p.category) and not STRONG.search(text):
        return False
    return bool(WANT.search(f"{text}\n{p.category}"))


def ago(published: datetime, now: datetime) -> str:
    minutes = max(1, int((now - published).total_seconds() // 60))
    return f"من {minutes} دقيقة" if minutes < 60 else f"من {minutes // 60} ساعة"


def alert_text(p: Project, v: Verdict | None, now: datetime) -> str:
    e = html.escape
    head = f"🟢 <b>FL.ru · {v.fit}/10</b>" if v else "🟡 <b>FL.ru</b>"
    meta = f"💰 {e(p.budget or 'بدون ميزانية')} · ⏱ {ago(p.published, now)}" + (" · для всех" if p.for_all else "")
    lines = [f"{head} · {e(p.category)}", f"<b>{e(p.title)}</b>", meta,
             f"<blockquote expandable>{e((p.description or p.summary)[:900])}</blockquote>"]
    if v:
        lines += [f"💡 {e(v.why)}",
                  f"✍️ في الفورم: السعر <b>{e(v.price)}</b> ₽ · المدة <b>{e(v.days)}</b> أيام",
                  "الرد جاهز للنسخ 👇", f"<pre>{e(v.reply[:2000])}</pre>"]
    else:
        lines.append("⚠️ الموديل ما ردّش، فمفيش رد جاهز. استخدم قالب FL.ru من «عدة الإطلاق».")
    return "\n".join(lines)


def first_number(value: object) -> str:
    """'9 000 ₽' → '9000', '5–7 дней' → '5'."""
    found = re.search(r"\d(?:[\d \u00a0]*\d)?", str(value or ""))
    return re.sub(r"\D", "", found.group(0)) if found else "—"


def parse_verdict(content: str) -> Verdict:
    found = re.search(r"\{.*\}", content, re.S)
    if not found:
        raise ValueError("no JSON in model output")
    data = json.loads(found.group(0), strict=False)   # the model may leave raw line breaks inside strings
    reply = re.sub(r"\*\*|__|```", "", str(data.get("reply") or "")).strip()
    if not reply:
        raise ValueError("empty reply")
    return Verdict(fit=int(float(data.get("fit") or 0)), why=str(data.get("why") or "").strip(),
                   price=first_number(data.get("price")), days=first_number(data.get("days")), reply=reply)


def night(now: datetime) -> bool:
    return now.astimezone(MSK).hour < 8


class Seen:
    """Project ids already handled: alerts are never repeated, even after a restart."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, ts REAL, fit INTEGER, "
                              "sent INTEGER, title TEXT)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    def meta(self, key: str, value: str | None = None) -> str | None:
        with self.lock:
            if value is not None:
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
                self.conn.commit()
                return value
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def known(self) -> set[str]:
        with self.lock:
            return {r[0] for r in self.conn.execute("SELECT id FROM seen")}

    def add(self, p: Project, fit: int, sent: bool) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?)",
                              (p.id, time.time(), fit, int(sent), p.title[:200]))
            self.conn.commit()

    def sent_since(self, ts: float) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM seen WHERE sent=1 AND ts>=?", (ts,)).fetchone()[0]

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def day_start(now: datetime) -> float:
    local = now.astimezone(MSK)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class FlWatch:
    def __init__(self, s: Settings, notifier: Notifier, chat_id: str, *, llm: Consultant | None = None,
                 client: httpx.AsyncClient | None = None, db: Path | None = None):
        self.s = s
        self.notifier = notifier
        self.chat_id = chat_id
        self.llm = llm or Consultant(s)
        self.client = client or httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": UA})
        self.seen = Seen(db or s.db_path.parent / "flwatch.sqlite")
        self.categories = [c.strip() for c in s.fl_categories.split(",") if c.strip()]
        self.profile = (ROOT / "app" / "flwatch_profile.md").read_text(encoding="utf-8").strip()
        self.verdicts: dict[str, Verdict] = {}   # kept while Telegram is unreachable, so the model isn't asked twice
        self.failures = 0

    async def fetch_feeds(self) -> tuple[list[Project], list[str]]:
        async def one(category: str) -> list[Project]:
            r = await self.client.get(f"{FL}/rss/all.xml", params={"category": category})
            r.raise_for_status()
            return parse_rss(r.content)

        results = await asyncio.gather(*(one(c) for c in self.categories), return_exceptions=True)
        projects: dict[str, Project] = {}
        errors = []
        for result in results:
            if isinstance(result, BaseException):
                errors.append(f"{type(result).__name__}: {str(result)[:120]}")
                continue
            for p in result:
                projects.setdefault(p.id, p)
        return list(projects.values()), errors

    async def description(self, p: Project) -> str:
        try:
            r = await self.client.get(p.url)
            r.raise_for_status()
            return page_description(r.text)
        except Exception as exc:
            log.warning("FL.ru project %s: %r", p.id, exc)
            return ""

    async def judge(self, p: Project) -> Verdict | None:
        if not self.s.llm_enabled:
            return None
        task = (f"Заказ: {p.title}\nРаздел: {p.category}\nБюджет: {p.budget or 'не указан'}\n\n"
                f"{(p.description or p.summary)[:3500]}")
        try:
            return parse_verdict(await self.llm._complete(
                [{"role": "system", "content": PROMPT.format(profile=self.profile, demo=DEMO_URL)},
                 {"role": "user", "content": task}], self.s.llm_model, 1200, 0.3))
        except Exception:
            log.exception("FL.ru draft failed for %s", p.id)
            return None

    async def say(self, text: str) -> None:
        try:
            await self.notifier.telegram(self.chat_id, text)
        except Exception as exc:
            log.warning("FL.ru watcher message not sent: %r", exc)
            STATUS["last_error"] = f"telegram: {type(exc).__name__}"

    async def tick(self, now: datetime | None = None) -> list[Project]:
        """One poll. Returns the projects sent to Telegram."""
        now = now or datetime.now(timezone.utc)
        projects, errors = await self.fetch_feeds()
        if errors and not projects:
            self.failures += 1
            STATUS.update(failures=self.failures, last_error=errors[0])
            if self.failures == WARN_AFTER:
                minutes = WARN_AFTER * self.s.fl_interval // 60
                await self.say(f"⚠️ FL.ru مش بيرد على السيرفر من حوالي {minutes} دقيقة ({html.escape(errors[0])}).\n"
                               "هفضل أحاول لوحدي، ولما يرجع هبعتلك.")
            return []
        if self.failures >= WARN_AFTER:
            await self.say("✅ FL.ru رجع يرد، والمراقبة شغالة تاني.")
        self.failures = 0
        STATUS.update(failures=0, last_ok=now.isoformat(timespec="seconds"))
        known = self.seen.known()
        if not known and projects:   # first start: everything already in the feeds counts as old
            for p in projects:
                self.seen.add(p, -1, False)
            await self.say(
                "✅ <b>مراقب FL.ru اشتغل</b>\n"
                f"بيراجع {len(self.categories)} أقسام كل {max(1, self.s.fl_interval // 60)} دقايق: "
                "AI، والبرمجة، والأوتوميشن، والماسنجر، والموبايل، والمواقع.\n"
                "أي مشروع جديد يناسبك هيوصلك هنا، ومعاه رد بالروسي جاهز للنسخ والسعر والمدة.\n"
                f"المشاريع اللي في الفيد دلوقتي ({len(projects)}) اعتبرتها قديمة.")
            STATUS.update(seen=self.seen.count(), sent_today=0)
            return []
        if not self.seen.meta("sample"):
            await self.sample(projects, now)
        sent = []
        for p in sorted((p for p in projects if p.id not in known), key=lambda p: p.published):
            if (now - p.published > MAX_AGE or not relevant(p)
                    or (p.budget_rub is not None and p.budget_rub < MIN_BUDGET)):
                self.seen.add(p, -1, False)
                continue
            if self.seen.sent_since(day_start(now)) >= self.s.fl_daily_max:
                self.seen.add(p, -2, False)
                continue
            if not p.description:
                p.description = await self.description(p) or p.summary
            v = self.verdicts.get(p.id) or await self.judge(p)
            if v is None and not STRONG.search(f"{p.title}\n{p.description}"):
                self.seen.add(p, -1, False)   # no model to judge it, and only a weak keyword match
                continue
            if v and v.fit < self.s.fl_min_fit:
                self.seen.add(p, v.fit, False)
                continue
            if v:
                self.verdicts[p.id] = v
            try:
                await self.notifier.telegram(self.chat_id, alert_text(p, v, now), silent=night(now),
                                             buttons=[("افتح المشروع على FL.ru", p.url)])
            except Exception as exc:          # stays unseen: retried on the next poll
                log.warning("FL.ru alert %s not sent: %r", p.id, exc)
                STATUS["last_error"] = f"telegram: {type(exc).__name__}"
                continue
            self.seen.add(p, v.fit if v else -1, True)
            self.verdicts.pop(p.id, None)
            sent.append(p)
        STATUS.update(seen=self.seen.count(), sent_today=self.seen.sent_since(day_start(now)))
        return sent

    async def sample(self, projects: list[Project], now: datetime) -> None:
        """Once: the best of today's fitting projects as an example alert — shows the format and proves that
        the whole chain (FL.ru → model → Telegram) works, without waiting for the next new project."""
        fresh = sorted((p for p in projects if now - p.published < timedelta(hours=24) and relevant(p)
                        and STRONG.search(f"{p.title}\n{p.summary}")), key=lambda p: p.published, reverse=True)[:3]
        best: tuple[Project, Verdict] | None = None
        for p in fresh:
            p.description = p.description or await self.description(p) or p.summary
            v = await self.judge(p)
            if v and (best is None or v.fit > best[1].fit):
                best = (p, v)
        if not best:
            STATUS["sample"] = "no fresh fitting project" if not fresh else "model gave no draft"
            self.seen.meta("sample", STATUS["sample"])
            return
        p, v = best
        intro = ("🧪 <b>مثال لشكل التنبيه</b>: ده أحسن مشروع مناسب من آخر ٢٤ ساعة، وغالبًا عليه ردود كتير. "
                 "التنبيهات الجاية هتوصلك أول ما المشروع ينزل.\n\n")
        try:
            await self.notifier.telegram(self.chat_id, intro + alert_text(p, v, now),
                                         buttons=[("افتح المشروع على FL.ru", p.url)])
        except Exception as exc:
            STATUS["sample"] = f"not sent: {type(exc).__name__}: {str(exc)[:120]}"
            return                       # tried again on the next poll
        STATUS["sample"] = f"sent: {p.id} fit {v.fit}"
        self.seen.meta("sample", STATUS["sample"])

    async def run(self) -> None:
        STATUS.update(enabled=True, categories=self.categories)
        while True:
            try:
                await self.tick()
            except Exception as exc:
                log.exception("FL.ru watcher round failed")
                STATUS["last_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            await asyncio.sleep(self.s.fl_interval)


def chat_for(s: Settings) -> str:
    if s.fl_chat_id:
        return s.fl_chat_id
    site = SiteRegistry(s.sites_dir).get(DEMO_SITE)
    return site.telegram_chat_id if site else ""


def start(s: Settings, notifier: Notifier) -> asyncio.Task | None:
    """Called on app startup. Without a Telegram bot or chat there is nowhere to send, so it stays off."""
    chat = chat_for(s)
    if not (s.fl_watch and s.telegram_token and chat):
        STATUS.update(enabled=False, reason="FL_WATCH=0" if not s.fl_watch else "no Telegram bot or chat")
        return None
    return asyncio.create_task(FlWatch(s, notifier, chat).run())


def main() -> None:
    import argparse

    from dotenv import load_dotenv

    from .notify import from_settings

    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="FL.ru watcher: one pass without sending")
    ap.add_argument("command", choices=["check"])
    ap.add_argument("--drafts", type=int, default=2, help="how many fitting projects to draft replies for")
    args = ap.parse_args()
    s = Settings.from_env()

    async def run() -> None:
        w = FlWatch(s, from_settings(s), chat_for(s), db=Path("/tmp/flwatch-check.sqlite"))
        projects, errors = await w.fetch_feeds()
        now = datetime.now(timezone.utc)
        print(f"feeds: {len(projects)} projects, errors: {errors or 'none'}, chat: {'yes' if w.chat_id else 'NO'}")
        fresh = sorted((p for p in projects if now - p.published < timedelta(hours=24) and relevant(p)),
                       key=lambda p: p.published, reverse=True)
        print(f"relevant in the last 24 h: {len(fresh)}")
        for i, p in enumerate(fresh):
            print(f"- {ago(p.published, now)} · {p.budget or '—'} · {p.category} · {p.title}\n  {p.url}")
            if i < args.drafts:
                p.description = await w.description(p) or p.summary
                v = await w.judge(p)
                print(f"  fit: {v.fit if v else '—'}  {v.why if v else ''}\n" + (v.reply if v else "") + "\n")
    asyncio.run(run())


if __name__ == "__main__":
    main()
