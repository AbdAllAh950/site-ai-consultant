"""Settings (environment) and per-site configuration (sites/<id>/site.json + kb.md).

One server serves many client sites: every site has its own knowledge base, look, texts,
where leads go (Telegram, MAX, email, webhook) and list of domains allowed to embed the widget.
Private per-site settings (chat ids, emails, a client's own MAX bot token) can live in
sites/<id>/site.local.json, which is merged over site.json and never committed.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("ru", "en", "ar")


@dataclass(frozen=True)
class Settings:
    llm_url: str = ""
    llm_auth: str = ""
    llm_model: str = ""
    llm_summary_model: str = ""          # optional cheaper model for lead summaries (e.g. yandexgpt-lite)
    llm_project: str = ""                # Yandex: folder id, sent as the OpenAI-Project header
    telegram_token: str = ""
    telegram_api: str = "https://api.telegram.org"
    max_token: str = ""                  # MAX messenger bot (platform-api2.max.ru); a site may bring its own
    max_api: str = "https://platform-api2.max.ru"
    smtp_host: str = ""                  # email for leads, e.g. smtp.yandex.ru:465 with an app password
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    sites_dir: Path = ROOT / "sites"
    db_path: Path = ROOT / "data" / "consultant.sqlite"
    public_url: str = ""                 # e.g. https://ai.example.ru — used in embed snippets
    keep_days: int = 90                  # dialogs older than this are deleted
    max_messages_per_session: int = 40
    max_requests_per_minute: int = 20    # per visitor IP

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_url and self.llm_auth and self.llm_model)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.getenv
        return cls(
            llm_url=e("LLM_URL", ""), llm_auth=e("LLM_AUTH", ""), llm_model=e("LLM_MODEL", ""),
            llm_summary_model=e("LLM_SUMMARY_MODEL", ""), llm_project=e("LLM_PROJECT", ""),
            telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_api=e("TELEGRAM_API_URL", "https://api.telegram.org"),
            max_token=e("MAX_BOT_TOKEN", ""), max_api=e("MAX_API_URL", "https://platform-api2.max.ru"),
            smtp_host=e("SMTP_HOST", ""), smtp_port=int(e("SMTP_PORT", "465")), smtp_user=e("SMTP_USER", ""),
            smtp_password=e("SMTP_PASSWORD", ""), smtp_from=e("SMTP_FROM", ""),
            sites_dir=Path(e("SITES_DIR", str(ROOT / "sites"))),
            db_path=Path(e("DB_PATH", str(ROOT / "data" / "consultant.sqlite"))),
            public_url=e("PUBLIC_URL", "").rstrip("/"),
            keep_days=int(e("KEEP_DAYS", "90")),
            max_messages_per_session=int(e("MAX_MESSAGES_PER_SESSION", "40")),
            max_requests_per_minute=int(e("MAX_REQUESTS_PER_MINUTE", "20")),
        )


@dataclass
class Site:
    id: str
    name: str
    about: str
    kb_text: str
    widget: dict                         # public: title, subtitle, accent, texts per language…
    telegram_chat_id: str = ""
    webhook_url: str = ""
    max_chat_id: str = ""                # MAX group chat with managers…
    max_user_id: str = ""                # …or one manager's MAX user id
    max_token: str = ""                  # the client's own MAX bot, if not the server's
    lead_emails: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)
    qualify: list[str] = field(default_factory=list)   # what to find out before the lead form
    glossary: dict[str, list[str]] = field(default_factory=dict)  # lang → "термин — перевод" lines

    def origin_allowed(self, origin: str | None) -> bool:
        if not self.allowed_origins or "*" in self.allowed_origins:
            return True
        if not origin:                    # same-origin requests (the demo page) send no Origin header
            return True
        return origin.rstrip("/") in {o.rstrip("/") for o in self.allowed_origins}

    def text(self, key: str, lang: str) -> str:
        block = self.widget.get(key, {})
        if isinstance(block, str):
            return block
        return block.get(lang) or block.get("ru") or ""

    def public_config(self) -> dict:
        """What the widget may see: never chat ids, webhooks or the knowledge base."""
        return {"id": self.id, "name": self.name, **self.widget}


SITE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
LOCAL = "site.local.json"


def load_site(sites_dir: Path, site_id: str) -> Site | None:
    if not SITE_ID.match(site_id or ""):
        return None
    folder = sites_dir / site_id
    cfg_file, kb_file = folder / "site.json", folder / "kb.md"
    if not cfg_file.exists() or not kb_file.exists():
        return None
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    local = folder / LOCAL
    if local.exists():
        cfg.update(json.loads(local.read_text(encoding="utf-8")))
    emails = cfg.get("lead_emails", [])
    return Site(
        id=site_id, name=cfg["name"], about=cfg.get("about", ""),
        kb_text=kb_file.read_text(encoding="utf-8"),
        widget=cfg.get("widget", {}),
        telegram_chat_id=str(cfg.get("telegram_chat_id", "")),
        webhook_url=cfg.get("webhook_url", ""),
        max_chat_id=str(cfg.get("max_chat_id", "")),
        max_user_id=str(cfg.get("max_user_id", "")),
        max_token=cfg.get("max_token", ""),
        lead_emails=[emails] if isinstance(emails, str) else list(emails),
        allowed_origins=cfg.get("allowed_origins", []),
        qualify=cfg.get("qualify", []),
        glossary=cfg.get("glossary", {}),
    )


class SiteRegistry:
    """Loads sites lazily and reloads a site when its files change, so edits need no restart."""

    def __init__(self, sites_dir: Path):
        self.sites_dir = sites_dir
        self._cache: dict[str, tuple[float, Site]] = {}

    def get(self, site_id: str) -> Site | None:
        folder = self.sites_dir / (site_id or "_")
        try:
            stamp = max((folder / "site.json").stat().st_mtime, (folder / "kb.md").stat().st_mtime)
        except (FileNotFoundError, NotADirectoryError):
            return None
        local = folder / LOCAL
        if local.exists():
            stamp = max(stamp, local.stat().st_mtime)
        cached = self._cache.get(site_id)
        if cached and cached[0] == stamp:
            return cached[1]
        site = load_site(self.sites_dir, site_id)
        if site:
            self._cache[site_id] = (stamp, site)
        return site
