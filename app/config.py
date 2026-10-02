"""Settings (environment) and per-site configuration (sites/<id>/site.json + kb.md).

One server serves many client sites: every site has its own knowledge base, look, texts,
Telegram chat for leads and list of domains allowed to embed the widget.
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
    allowed_origins: list[str] = field(default_factory=list)
    qualify: list[str] = field(default_factory=list)   # what to find out before the lead form

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


def load_site(sites_dir: Path, site_id: str) -> Site | None:
    if not SITE_ID.match(site_id or ""):
        return None
    folder = sites_dir / site_id
    cfg_file, kb_file = folder / "site.json", folder / "kb.md"
    if not cfg_file.exists() or not kb_file.exists():
        return None
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    return Site(
        id=site_id, name=cfg["name"], about=cfg.get("about", ""),
        kb_text=kb_file.read_text(encoding="utf-8"),
        widget=cfg.get("widget", {}),
        telegram_chat_id=str(cfg.get("telegram_chat_id", "")),
        webhook_url=cfg.get("webhook_url", ""),
        allowed_origins=cfg.get("allowed_origins", []),
        qualify=cfg.get("qualify", []),
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
        cached = self._cache.get(site_id)
        if cached and cached[0] == stamp:
            return cached[1]
        site = load_site(self.sites_dir, site_id)
        if site:
            self._cache[site_id] = (stamp, site)
        return site
