"""HTTP API for the chat widget.

GET  /widget.js                      the widget (one <script> tag on any site, Tilda included)
GET  /api/sites/{site}/config        public look and texts of a site's widget
POST /api/chat                       one visitor message → consultant reply (+ whether to show the lead form)
POST /api/lead                       contacts from the form → Telegram / MAX / email / webhook, stored in SQLite
GET  /embed/{site}                   the snippet to paste into the client's site
GET  /demo/                          demo landing page with the widget
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import flwatch
from .config import ROOT, Settings, Site, SiteRegistry
from .consultant import Consultant, detect_lang
from .knowledge import KnowledgeBase
from .limits import RateLimiter
from .notify import Notifier, from_settings, telegram_text
from .store import Store

load_dotenv()
log = logging.getLogger("consultant")
SESSION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DONE = {
    "ru": "Спасибо! Заявка у менеджера, он свяжется с вами в ближайшее время.",
    "en": "Thank you! Your request is with our manager, they will contact you shortly.",
    "ar": "شكرًا لك! وصل طلبك إلى المدير وسيتواصل معك قريبًا.",
}
LIMIT = {
    "ru": "Похоже, вопросов много 🙂 Оставьте контакты — менеджер ответит на всё лично.",
    "en": "Lots of questions 🙂 Leave your contacts and a manager will answer everything personally.",
    "ar": "يبدو أن لديك أسئلة كثيرة 🙂 اترك بياناتك وسيجيبك المدير شخصيًا.",
}


class ChatIn(BaseModel):
    site: str = Field(max_length=42)
    session: str = Field(max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    lang: str = Field(default="ru", max_length=8)
    page: str = Field(default="", max_length=500)


class LeadIn(BaseModel):
    site: str = Field(max_length=42)
    session: str = Field(max_length=64)
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=5, max_length=40)
    consent: bool = False
    comment: str = Field(default="", max_length=1000)
    lang: str = Field(default="ru", max_length=8)
    page: str = Field(default="", max_length=500)

    @field_validator("name", "comment")
    @classmethod
    def squash(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, v: str) -> str:
        if not 9 <= len(re.sub(r"\D", "", v)) <= 15:
            raise ValueError("phone must have 9–15 digits")
        return v


def normalize_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw)
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10 and d[0] == "9":
        return "+7" + d
    return "+" + d


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def build_app(settings: Settings | None = None, consultant: Consultant | None = None,
              notifier: Notifier | None = None, store: Store | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    sites = SiteRegistry(s.sites_dir)
    consultant = consultant or Consultant(s)
    notifier = notifier or from_settings(s)
    store = store or Store(s.db_path)
    store.purge(s.keep_days)
    limiter = RateLimiter(s.max_requests_per_minute)
    kb_cache: dict[str, tuple[Site, KnowledgeBase]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        watcher = flwatch.start(s, notifier)     # FL.ru projects → Telegram (only on the owner's server)
        yield
        if watcher:
            watcher.cancel()

    app = FastAPI(title="AI-консультант для сайта", version="1.0", lifespan=lifespan)
    # The widget runs on clients' domains; each site's own list of domains is checked per request below.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    def site_or_404(site_id: str, request: Request) -> Site:
        site = sites.get(site_id)
        if not site:
            raise HTTPException(404, "unknown site")
        if not site.origin_allowed(request.headers.get("origin")):
            raise HTTPException(403, "this domain is not allowed for the site")
        return site

    def kb_for(site: Site) -> KnowledgeBase:
        cached = kb_cache.get(site.id)
        if not cached or cached[0] is not site:
            cached = kb_cache[site.id] = (site, KnowledgeBase(site.kb_text))
        return cached[1]

    @app.get("/health")
    async def health():
        return {"ok": True, "version": os.getenv("APP_VERSION", "dev"), "ai": s.llm_enabled,
                "telegram": bool(s.telegram_token), "max": bool(s.max_token), "email": notifier.smtp.enabled,
                "flwatch": {k: v for k, v in flwatch.STATUS.items() if k != "categories"}}

    @app.get("/api/_probe")
    async def probe(request: Request):
        """Temporary: checks that custom headers and cookies survive the way from the browser to the app."""
        from fastapi.responses import JSONResponse
        seen = {k: (v if k in ("browser-id", "x-forwarded-proto", "x-forwarded-for", "user-agent", "via") else "…")
                for k, v in request.headers.items()}
        seen["cookie-names"] = list(request.cookies)
        r = JSONResponse(seen)
        r.set_cookie("probe", "1", max_age=600, secure=True, httponly=True, samesite="lax")
        return r

    @app.get("/api/sites/{site_id}/config")
    async def site_config(site_id: str, request: Request):
        return site_or_404(site_id, request).public_config()

    @app.post("/api/chat")
    async def chat(body: ChatIn, request: Request):
        site = site_or_404(body.site, request)
        if not SESSION.match(body.session):
            raise HTTPException(422, "bad session id")
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "too many requests")
        lang = detect_lang(body.message, body.lang[:2])
        if store.count_user_messages(site.id, body.session) >= s.max_messages_per_session:
            return {"reply": LIMIT[lang], "show_form": True, "lang": lang, "by": "limit"}
        history = store.history(site.id, body.session)
        reply = await consultant.answer(site, kb_for(site), history, body.message, body.lang[:2])
        store.add_message(site.id, body.session, "user", body.message, reply.lang)
        store.add_message(site.id, body.session, "assistant", reply.text, reply.lang)
        return {"reply": reply.text, "show_form": reply.show_form, "lang": reply.lang, "by": reply.by}

    @app.post("/api/lead")
    async def lead(body: LeadIn, request: Request):
        site = site_or_404(body.site, request)
        if not SESSION.match(body.session):
            raise HTTPException(422, "bad session id")
        if not body.consent:
            raise HTTPException(422, "consent to personal data processing is required")
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "too many requests")
        lang = body.lang[:2] if body.lang[:2] in DONE else "ru"
        phone = normalize_phone(body.phone)
        done = site.text("lead_done", lang) or DONE[lang]
        if store.recent_lead(site.id, body.session, phone):  # double click / resend: don't ping twice
            return {"ok": True, "message": done}
        history = store.history(site.id, body.session)
        card = await consultant.summarize(history)
        data = {"name": body.name, "phone": phone, "comment": body.comment, "page": body.page, "lang": lang}
        text = telegram_text(site.name, data, card, history)
        channels = {
            "telegram": notifier.telegram(site.telegram_chat_id, text),
            "max": notifier.max(text, site.max_chat_id, site.max_user_id, site.max_token),
            "email": notifier.email(site.lead_emails, f"Заявка из AI-чата: {body.name} · {site.name}", text),
            "webhook": notifier.webhook(site.webhook_url, {
                "site": site.id, "site_name": site.name, **data, **card, "source": "ai-chat",
                "dialog": [{"role": m["role"], "text": m["content"]} for m in history]}),
        }
        # every channel on its own: one failing (Telegram blocked, SMTP down) never loses the lead
        results = await asyncio.gather(*channels.values(), return_exceptions=True)
        delivered = {}
        for name, result in zip(channels, results):
            if isinstance(result, BaseException):
                log.error("%s delivery failed for %s: %r", name, site.id, result)
                result = False
            delivered[name] = result
        lead_id = store.add_lead(site.id, body.session, body.name, phone, card, body.page, lang, delivered)
        store.add_message(site.id, body.session, "system", f"lead #{lead_id} sent", lang)
        return {"ok": True, "message": done}

    @app.get("/embed/{site_id}", response_class=PlainTextResponse)
    async def embed(site_id: str, request: Request):
        if not sites.get(site_id):
            raise HTTPException(404, "unknown site")
        base = s.public_url or str(request.base_url).rstrip("/")
        return f'<script src="{base}/widget.js" data-site="{site_id}" defer></script>\n'

    @app.get("/widget.js")
    async def widget():
        return FileResponse(ROOT / "widget" / "widget.js", media_type="application/javascript",
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/")
    async def root():
        return RedirectResponse("/demo/")

    demo = ROOT / "demo"
    if demo.exists():
        app.mount("/demo", StaticFiles(directory=demo, html=True), name="demo")
    return app


app = build_app()
