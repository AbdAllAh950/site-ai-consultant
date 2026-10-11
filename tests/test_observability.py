"""Tracing (OpenTelemetry GenAI conventions) and evaluations."""
import asyncio
import json
import shutil
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import evals, tracing
from app.config import ROOT, Settings, SiteRegistry
from app.consultant import Consultant, Reply
from app.knowledge import WHOLE_KB_LIMIT, KnowledgeBase
from app.main import build_app
from app.notify import Notifier
from app.store import Store
from tests.fakes import fake_llm, recorder

SITE = "zeleny-kontur"
CHAT = {"site": SITE, "session": "trace-session-001", "lang": "ru", "page": "https://example.ru/"}


@pytest.fixture(scope="module")
def spans():
    tracing.setup()
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def s(tmp_path):
    sites = tmp_path / "sites"
    shutil.copytree(ROOT / "sites", sites)
    return Settings(llm_url="https://llm.api.cloud.yandex.net/v1/chat/completions", llm_auth="Api-Key k",
                    llm_model="gpt://b1gfolder/yandexgpt/latest", telegram_token="123:ABC",
                    telegram_api="https://tg.test", sites_dir=sites, db_path=tmp_path / "db.sqlite",
                    max_requests_per_minute=100)


def usage_llm():
    """An OpenAI-compatible model that also reports usage and finish reason, like YandexGPT does."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "resp-1", "model": "yandexgpt/latest",
            "choices": [{"message": {"role": "assistant", "content": "Рулонный газон — от 590 ₽ за м²."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 25, "total_tokens": 1225}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def by_name(exporter, prefix):
    return [sp for sp in exporter.get_finished_spans() if sp.name.startswith(prefix)]


# ---------- tracing ----------
def test_chat_turn_is_one_trace_with_genai_attributes(s, spans):
    spans.clear()
    app = build_app(s, Consultant(s, usage_llm()), Notifier("", "https://tg.test", recorder([])), Store(s.db_path))
    r = TestClient(app).post("/api/chat", json={**CHAT, "message": "Сколько стоит газон?"})
    assert r.status_code == 200
    agent, = by_name(spans, "invoke_agent")
    retrieval, = by_name(spans, "retrieval")
    chat, = by_name(spans, "chat ")
    assert chat.name == "chat yandexgpt/latest"                  # folder id is not in the trace
    assert chat.parent.span_id == agent.context.span_id == retrieval.parent.span_id
    a = chat.attributes
    assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.provider.name"] == "yandex"
    assert a["gen_ai.request.model"] == "yandexgpt/latest" and a["gen_ai.request.temperature"] == 0.2
    assert a["gen_ai.usage.input_tokens"] == 1200 and a["gen_ai.usage.output_tokens"] == 25
    assert a["gen_ai.response.finish_reasons"] == ("stop",) and a["gen_ai.response.id"] == "resp-1"
    assert agent.attributes["gen_ai.conversation.id"] == CHAT["session"]
    assert agent.attributes["app.reply.by"] == "ai"
    assert retrieval.attributes["gen_ai.data_source.id"] == f"kb:{SITE}"
    assert retrieval.attributes["app.kb.top_sections"][0] == "Газон"
    # visitor's words and prompts are not recorded by default
    everything = json.dumps([dict(sp.attributes) for sp in spans.get_finished_spans()], ensure_ascii=False)
    assert "Сколько стоит газон" not in everything and "590" not in everything


def test_content_is_captured_only_when_asked(s, spans, monkeypatch):
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    spans.clear()
    site = SiteRegistry(s.sites_dir).get(SITE)
    asyncio.run(Consultant(s, usage_llm()).answer(site, KnowledgeBase(site.kb_text), [], "Сколько стоит газон?"))
    chat, = by_name(spans, "chat ")
    out = json.loads(chat.attributes["gen_ai.output.messages"])
    assert out[0]["parts"][0]["content"].startswith("Рулонный газон")
    assert "Сколько стоит газон" in chat.attributes["gen_ai.input.messages"]


def test_failed_llm_call_has_error_type_and_health_counts_it(s, spans):
    spans.clear()
    tracing.RECENT.rows.clear()
    app = build_app(s, Consultant(s, fake_llm(fail=True)), Notifier("", "https://tg.test", recorder([])),
                    Store(s.db_path))
    client = TestClient(app)
    assert client.post("/api/chat", json={**CHAT, "message": "Сколько стоит газон?"}).json()["by"] == "fallback"
    chat, = by_name(spans, "chat ")
    assert chat.attributes["error.type"] == "500" and not chat.status.is_ok
    llm = client.get("/health").json()["llm"]
    assert llm["calls_1h"] == 1 and llm["errors_1h"] == 1 and llm["p95_ms"] >= 0


def test_lead_delivery_spans_per_channel(s, spans):
    spans.clear()
    cfg = s.sites_dir / SITE / "site.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    cfg.write_text(json.dumps({**data, "telegram_chat_id": "-100500"}, ensure_ascii=False), encoding="utf-8")
    app = build_app(s, Consultant(s, fake_llm()), Notifier(s.telegram_token, s.telegram_api, recorder([])),
                    Store(s.db_path))
    r = TestClient(app).post("/api/lead", json={**CHAT, "name": "Анна", "phone": "+7 921 123-45-67",
                                                "consent": True})
    assert r.status_code == 200
    lead, = by_name(spans, "lead ")
    delivered = {sp.attributes["app.channel"]: sp.attributes["app.delivered"] for sp in by_name(spans, "deliver_lead")}
    assert delivered == {"telegram": True, "max": False, "email": False, "webhook": False}
    assert all(sp.parent.span_id == lead.context.span_id for sp in by_name(spans, "deliver_lead"))


def test_helpers():
    assert tracing.model_label("gpt://b1g/yandexgpt-lite/latest") == "yandexgpt-lite/latest"
    assert tracing.model_label("gpt-4o-mini") == "gpt-4o-mini"
    assert tracing.provider_name("https://api.openai.com/v1/chat/completions") == "openai"
    assert tracing.provider_name("https://llm.example.org/v1") == "llm.example.org"
    assert tracing.error_type(ValueError("x")) == "ValueError"


# ---------- knowledge selection ----------
def test_foreign_question_to_a_large_russian_base_gets_sections_not_just_the_intro():
    big = "Бюро.\n" + "\n".join(f"## Раздел {i}\n" + "текст " * 300 for i in range(10))
    context, hits, whole = KnowledgeBase(big).select("How much is a lawn?")
    assert not hits and not whole and "## Раздел 0" in context and len(context) <= WHOLE_KB_LIMIT


# ---------- evals ----------
def test_normalize_numbers_and_language():
    assert "85000" in evals.normalize("85,000") and "1,5" in evals.normalize("1,5 месяца")
    assert "85000" in evals.normalize("от 85 000 ₽") and "85000" in evals.normalize("٨٥٬٠٠٠ روبل")
    assert evals.numbers("от 1,5 до 2 месяцев, 15 000 ₽") == {"1.5", "2", "15000"}
    assert evals.script_lang("Zelyony Kontur: turf from 590 rub, Зелёный контур") == "en"
    assert evals.script_lang("العشب من 590 روبل لدى Зелёный контур") == "ar"


def test_check_catches_invented_numbers_wrong_language_and_markdown():
    kb = (ROOT / "sites" / SITE / "kb.md").read_text(encoding="utf-8")
    case = evals.Case(id="x", q="Сколько стоит газон?", include=["590"], lead=False)
    good = Reply("Рулонный газон — от 590 ₽ за м², укладка 3–5 дней.", False, "ru", "ai")
    assert evals.check(case, good, kb) == []
    invented = Reply("Рулонный газон — от 590 ₽, сейчас скидка 25%.", False, "ru", "ai")
    assert evals.check(case, invented, kb) == ["grounded: numbers not in the knowledge base ['25']"]
    wrong = Reply("**Lawn** costs 590 rub.", True, "en", "fallback")
    failed = " | ".join(evals.check(case, wrong, kb))
    assert "lead: expected False" in failed and "language: expected ru" in failed
    assert "format: markdown" in failed and "model: reply came from 'fallback'" in failed


def test_eval_cases_are_valid_and_russian_retrieval_does_not_regress():
    cases = evals.load_cases(SITE)
    kb = KnowledgeBase((ROOT / "sites" / SITE / "kb.md").read_text(encoding="utf-8"))
    titles = {c.title for c in kb.chunks}
    assert len(cases) >= 20 and len({c.id for c in cases}) == len(cases)
    assert {c.section for c in cases if c.section} <= titles          # cases point at sections that exist
    report = evals.retrieval_report(kb, cases)
    assert report["by_lang"]["ru"]["hit@3"] >= 0.9                   # keyword search works for Russian…
    assert report["by_lang"]["en"]["hit@3"] == 0                      # …and is blind across languages: a known
    assert report["by_lang"]["ar"]["hit@3"] == 0                      # gap (the next step is embeddings)


def test_behaviour_report_runs_cases_through_the_consultant(s, spans):
    spans.clear()
    site = SiteRegistry(s.sites_dir).get(SITE)
    cases = [evals.Case(id="lawn", q="Сколько стоит рулонный газон?", include=["590"]),
             evals.Case(id="lead", q="Хочу расчёт участка", lead=True),
             evals.Case(id="unanswerable", q="Сколько стоит бассейн?", include=["бассейн"])]
    result = asyncio.run(evals.behaviour_report(Consultant(s, fake_llm()), site, KnowledgeBase(site.kb_text),
                                                cases, pause_s=0))
    assert (result["passed"], result["total"]) == (2, 3)
    assert [r["passed"] for r in result["cases"]] == [True, True, False]
    run, = by_name(spans, "evaluate")
    labels = [e.attributes["gen_ai.evaluation.score.label"] for e in run.events]
    assert labels == ["pass", "pass", "fail"] and run.events[0].name == "gen_ai.evaluation.result"


def test_after_deploy_runs_once_per_fingerprint_and_reports(s, tmp_path):
    folder = tmp_path / "evals"
    folder.mkdir()
    (folder / f"{SITE}.json").write_text(json.dumps({"cases": [
        {"id": "lawn", "q": "Сколько стоит рулонный газон?", "section": "Газон", "include": ["590"]}]}),
        encoding="utf-8")
    s = Settings(**{**s.__dict__, "eval_on_deploy": True, "fl_chat_id": "42"})
    calls, sent = [], []
    consultant = Consultant(s, fake_llm(calls))
    notifier = Notifier(s.telegram_token, s.telegram_api, recorder(sent))
    sites = SiteRegistry(s.sites_dir)

    def run():
        asyncio.run(evals.run_after_deploy(s, consultant, sites, notifier, delay_s=0, folder=folder))

    run()
    assert len(calls) == 1 and evals.STATUS[SITE]["passed"] == 1 and evals.STATUS[SITE]["retrieval_hit@3"] == 1.0
    assert "Eval после деплоя" in sent[0][1]["text"] and sent[0][1]["chat_id"] == "42"
    run()                                                    # nothing changed → no new model calls
    assert len(calls) == 1 and len(sent) == 1
    kb = s.sites_dir / SITE / "kb.md"
    kb.write_text(kb.read_text(encoding="utf-8") + "\n## Новое\nтекст", encoding="utf-8")
    run()                                                    # knowledge base changed → evaluated again
    assert len(calls) == 2 and len(sent) == 2
    stored = json.loads((evals.results_dir(s) / f"{SITE}.json").read_text(encoding="utf-8"))
    assert stored["cases"][0]["reply"].startswith("Рулонный газон")


def test_evals_start_only_when_enabled(s):
    assert evals.start(s, SimpleNamespace(), SiteRegistry(s.sites_dir)) is None   # eval_on_deploy off
