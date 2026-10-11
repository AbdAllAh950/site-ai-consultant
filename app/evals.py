"""Evaluations: does the consultant still behave after a change to the prompt, the knowledge base or the model?

The cases live in evals/<site>.json. Two levels:

retrieval (offline, no model)  is the right knowledge-base section in the top 3 for each question?
                               hit@1, hit@3 and MRR, per language; part of the test suite, so every commit runs it.
behaviour (live model)         each question goes through the real consultant (prompt + model + lead logic)
                               and the reply is checked by rules:
    include      at least one of these must be in the reply (numbers compared without thousand separators)
    exclude      none of these may be in the reply (e.g. leaking the system prompt)
    lead         the lead form must (true) or must not (false) be offered
    language     the reply is in the visitor's language
    grounded     every number in the reply exists in the knowledge base or in the question —
                 catches invented prices, terms and discounts
    format       plain text, no markdown, not longer than max_chars

python -m app.evals <site>          retrieval only
python -m app.evals <site> --live   + behaviour against the configured model (costs ~1 model call per case)

On the server the live run starts by itself after a deploy whenever the prompt, knowledge base, site settings,
model or cases changed (a fingerprint of all of them), so every behaviour change gets evaluated once.
The result is in /health → "evals" and goes to the owner's Telegram.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import consultant as consultant_module
from . import tracing
from .config import ROOT, Settings, Site, SiteRegistry
from .knowledge import KnowledgeBase

log = logging.getLogger(__name__)
EVALS_DIR = ROOT / "evals"
VERSION = "1"                                  # bump when the checks themselves change → forces a re-run

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
THOUSANDS = re.compile(r"(?<=\d)[\s  ,٬'](?=\d{3}(?!\d))")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
MARKDOWN = re.compile(r"\*\*|__|^\s*#+\s|^\s*[-*•]\s|^\s*\d+[.)]\s", re.M)


@dataclass
class Case:
    id: str
    q: str
    section: str = ""                 # expected knowledge-base section ("" — none expected)
    lang: str = ""                    # expected reply language ("" — the question's)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    lead: bool | None = None
    grounded: bool = True
    max_chars: int = 700


def load_cases(site_id: str, folder: Path = EVALS_DIR) -> list[Case]:
    path = folder / f"{site_id}.json"
    if not path.exists():
        return []
    return [Case(**c) for c in json.loads(path.read_text(encoding="utf-8"))["cases"]]


def normalize(text: str) -> str:
    """Lowercase, Western digits, no thousand separators: «85 000», «85,000» and «٨٥٬٠٠٠» all become 85000."""
    text = text.translate(ARABIC_DIGITS).lower().replace("ё", "е")
    return THOUSANDS.sub("", text)


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in NUMBER.findall(normalize(text))}


def script_lang(text: str) -> str:
    """The language a reply is written in, by its dominant script (a Latin brand name in Arabic text is fine)."""
    counts = {"ar": len(re.findall(r"[؀-ۿ]", text)), "ru": len(re.findall(r"[А-Яа-яЁё]", text)),
              "en": len(re.findall(r"[A-Za-z]", text))}
    return max(counts, key=counts.get) if any(counts.values()) else ""


# ---------- retrieval (offline) ----------
def retrieval_report(kb: KnowledgeBase, cases: list[Case]) -> dict:
    rows, by_lang = [], {}
    for c in cases:
        if not c.section:
            continue
        titles = [chunk.title for _, chunk in kb.search(c.q, k=5)]
        rank = titles.index(c.section) + 1 if c.section in titles else 0
        lang = c.lang or consultant_module.detect_lang(c.q)
        rows.append({"id": c.id, "lang": lang, "rank": rank, "top": titles[:3]})
        by_lang.setdefault(lang, []).append(rank)
    return {"overall": _ranks(r["rank"] for r in rows), "by_lang": {k: _ranks(v) for k, v in by_lang.items()},
            "cases": rows}


def _ranks(ranks) -> dict:
    ranks = list(ranks)
    n = len(ranks) or 1
    return {"n": len(ranks), "hit@1": round(sum(r == 1 for r in ranks) / n, 3),
            "hit@3": round(sum(1 <= r <= 3 for r in ranks) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3)}


# ---------- behaviour (live model) ----------
def check(case: Case, reply, kb_text: str) -> list[str]:
    """Failed checks for one reply (empty list = passed)."""
    text, failed = reply.text, []
    norm = normalize(text)
    if case.include and not any(normalize(s) in norm for s in case.include):
        failed.append(f"include: none of {case.include}")
    for s in case.exclude:
        if normalize(s) in norm:
            failed.append(f"exclude: {s!r}")
    if case.lead is not None and reply.show_form != case.lead:
        failed.append(f"lead: expected {case.lead}, got {reply.show_form}")
    expected = case.lang or consultant_module.detect_lang(case.q)
    if script_lang(text) != expected:
        failed.append(f"language: expected {expected}, got {script_lang(text) or '?'}")
    if case.grounded:
        unknown = sorted(numbers(text) - numbers(kb_text) - numbers(case.q))
        if unknown:
            failed.append(f"grounded: numbers not in the knowledge base {unknown}")
    if MARKDOWN.search(text):
        failed.append("format: markdown")
    if len(text) > case.max_chars:
        failed.append(f"format: {len(text)} chars > {case.max_chars}")
    if reply.by != "ai":
        failed.append(f"model: reply came from {reply.by!r}")
    return failed


async def behaviour_report(consultant, site: Site, kb: KnowledgeBase, cases: list[Case],
                           pause_s: float = 0.5) -> dict:
    rows = []
    with tracing.tracer.start_as_current_span(f"evaluate consultant:{site.id}") as span:
        for c in cases:
            started = time.monotonic()
            reply = await consultant.answer(site, kb, [], c.q, c.lang or "ru", conversation=f"eval-{c.id}")
            failed = check(c, reply, site.kb_text)
            rows.append({"id": c.id, "passed": not failed, "failed": failed, "reply": reply.text,
                         "show_form": reply.show_form, "ms": round((time.monotonic() - started) * 1000)})
            span.add_event("gen_ai.evaluation.result", {
                "gen_ai.evaluation.name": c.id, "gen_ai.evaluation.score.value": 0.0 if failed else 1.0,
                "gen_ai.evaluation.score.label": "fail" if failed else "pass",
                **({"gen_ai.evaluation.explanation": "; ".join(failed)} if failed else {})})
            await asyncio.sleep(pause_s)
        passed = sum(r["passed"] for r in rows)
        span.set_attribute("app.eval.passed", passed)
        span.set_attribute("app.eval.total", len(rows))
    return {"passed": passed, "total": len(rows), "score": round(passed / (len(rows) or 1), 3), "cases": rows}


# ---------- fingerprint & the run after a deploy ----------
def fingerprint(s: Settings, site: Site, cases_file: Path) -> str:
    """Changes whenever anything that shapes the consultant's behaviour changes."""
    parts = [VERSION, consultant_module.SYSTEM, str(consultant_module.INTENT.pattern), s.llm_model, site.kb_text,
             site.name, site.about, json.dumps(site.qualify, ensure_ascii=False),
             json.dumps(site.glossary, ensure_ascii=False, sort_keys=True),
             cases_file.read_text(encoding="utf-8") if cases_file.exists() else ""]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


STATUS: dict[str, dict] = {}          # site → summary of the last run, for /health


def results_dir(s: Settings) -> Path:
    return s.db_path.parent / "eval-results"


def summary(result: dict) -> dict:
    keys = ("fingerprint", "at", "passed", "total", "score")
    out = {k: result[k] for k in keys if k in result}
    out["retrieval_hit@3"] = result.get("retrieval", {}).get("overall", {}).get("hit@3")
    out["failed"] = [r["id"] for r in result.get("cases", []) if not r["passed"]]
    return out


def telegram_report(site: Site, result: dict) -> str:
    lines = [f"🧪 <b>Eval после деплоя</b> · {html.escape(site.name)}",
             f"Поведение: <b>{result['passed']}/{result['total']}</b> · поиск hit@3 "
             f"{result['retrieval']['overall']['hit@3']}"]
    for r in result["cases"]:
        if not r["passed"]:
            lines.append(f"❌ {html.escape(r['id'])}: " + html.escape("; ".join(r["failed"])[:300]))
    return "\n".join(lines)


async def run_after_deploy(s: Settings, consultant, sites: SiteRegistry, notifier=None,
                           delay_s: float = 20, folder: Path = EVALS_DIR) -> None:
    """For every site with cases: run the live evals once per fingerprint, keep the result, tell the owner."""
    await asyncio.sleep(delay_s)
    out_dir = results_dir(s)
    for path in sorted(folder.glob("*.json")):
        site = sites.get(path.stem)
        if not site:
            continue
        stamp = fingerprint(s, site, path)
        last_file = out_dir / f"{site.id}.json"
        try:
            last = json.loads(last_file.read_text(encoding="utf-8")) if last_file.exists() else {}
        except ValueError:
            last = {}
        if last.get("fingerprint") == stamp:
            STATUS[site.id] = summary(last)
            continue
        try:
            cases, kb = load_cases(site.id, folder), KnowledgeBase(site.kb_text)
            STATUS[site.id] = {"fingerprint": stamp, "running": True}
            result = {"fingerprint": stamp, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "model": s.llm_model,
                      "retrieval": retrieval_report(kb, cases),
                      **await behaviour_report(consultant, site, kb, cases)}
            out_dir.mkdir(parents=True, exist_ok=True)
            last_file.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            STATUS[site.id] = summary(result)
            if notifier and s.fl_chat_id:
                await notifier.telegram(s.fl_chat_id, telegram_report(site, result), silent=True)
        except Exception as exc:
            log.exception("evals failed for %s", site.id)
            STATUS[site.id] = {"fingerprint": stamp, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def start(s: Settings, consultant, sites: SiteRegistry, notifier=None) -> asyncio.Task | None:
    if not (s.eval_on_deploy and s.llm_enabled):
        return None
    return asyncio.create_task(run_after_deploy(s, consultant, sites, notifier))


# ---------- CLI ----------
def main() -> None:
    import argparse

    from dotenv import load_dotenv

    from .consultant import Consultant

    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Evaluate a site's consultant")
    ap.add_argument("site")
    ap.add_argument("--live", action="store_true", help="also ask the configured model (~1 call per case)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args()
    s = Settings.from_env()
    site = SiteRegistry(s.sites_dir).get(args.site)
    cases = load_cases(args.site)
    if not site or not cases:
        raise SystemExit(f"no site or no evals/{args.site}.json")
    kb = KnowledgeBase(site.kb_text)
    result = {"retrieval": retrieval_report(kb, cases)}
    if args.live:
        if not s.llm_enabled:
            raise SystemExit("--live needs LLM_URL, LLM_AUTH and LLM_MODEL")
        tracing.setup()
        result |= asyncio.run(behaviour_report(Consultant(s), site, kb, cases))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    r = result["retrieval"]
    print(f"retrieval  overall {r['overall']}")
    for lang, m in r["by_lang"].items():
        print(f"           {lang:2} {m}")
    for row in r["cases"]:
        if row["rank"] != 1:
            print(f"           rank {row['rank'] or '-'} {row['id']}: top {row['top']}")
    if args.live:
        print(f"behaviour  {result['passed']}/{result['total']} passed")
        for row in result["cases"]:
            mark = "✓" if row["passed"] else "✗"
            print(f"  {mark} {row['id']:18} {row['ms']:>5} ms  {'; '.join(row['failed'])}")
        sys.exit(0 if result["passed"] == result["total"] else 1)


if __name__ == "__main__":
    main()
