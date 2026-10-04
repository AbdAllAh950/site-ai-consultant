import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.consultant import Consultant, clean_reply, detect_lang
from app.knowledge import KnowledgeBase
from app.limits import RateLimiter
from app.main import build_app, normalize_phone
from app.notify import Notifier, Smtp, plain, telegram_text
from app.report import render
from app.store import Store
from tests.fakes import fake_llm, recorder

SITE = "zeleny-kontur"
CHAT = {"site": SITE, "session": "test-session-0001", "lang": "ru", "page": "https://example.ru/"}
LEAD = {"site": SITE, "session": "test-session-0001", "name": "Анна", "phone": "8 (921) 123-45-67",
        "consent": True, "lang": "ru", "page": "https://example.ru/"}


@pytest.fixture
def env(tmp_path):
    sites = tmp_path / "sites"
    shutil.copytree(ROOT / "sites", sites)
    cfg = sites / SITE / "site.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data.update(telegram_chat_id="-100500", webhook_url="https://crm.test/hook")
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    s = Settings(llm_url="https://llm.test/v1/chat/completions", llm_auth="Api-Key k",
                 llm_model="gpt://folder/yandexgpt/latest", telegram_token="123:ABC",
                 telegram_api="https://tg.test", sites_dir=sites, db_path=tmp_path / "db.sqlite",
                 public_url="https://ai.example.ru", max_requests_per_minute=100)
    return s


def make(s: Settings, calls=None, sent=None, llm_fail=False, tg_fail=False):
    sent = sent if sent is not None else []
    app = build_app(s, Consultant(s, fake_llm(calls, fail=llm_fail)),
                    Notifier(s.telegram_token, s.telegram_api, recorder(sent, fail=tg_fail)), Store(s.db_path))
    return TestClient(app), sent


# ---------- pieces ----------
def test_knowledge_base_parsing_and_search():
    kb = KnowledgeBase((ROOT / "sites" / SITE / "kb.md").read_text(encoding="utf-8"))
    assert kb.intro.startswith("Ландшафтное бюро") and len(kb.chunks) == 15
    assert kb.search("сколько стоит рулонный газон")[0][1].title == "Газон"
    assert "## Автополив" in kb.context_for("газон")  # small base → sent whole


def test_large_base_is_narrowed():
    big = "Intro\n" + "\n".join(f"## Тема {i}\n" + "слово " * 400 for i in range(10)) + "\n## Газон\nот 590 ₽"
    ctx = KnowledgeBase(big).context_for("газон")
    assert "## Газон" in ctx and len(ctx) < 9000


@pytest.mark.parametrize("text,lang", [("Сколько стоит газон?", "ru"), ("كم سعر العشب؟", "ar"),
                                       ("How much is a lawn?", "en"), ("123", "ru")])
def test_detect_lang(text, lang):
    assert detect_lang(text) == lang


def test_clean_reply():
    assert clean_reply("**Посчитаем** после замера. [[LEAD]]") == ("Посчитаем после замера.", True)
    assert clean_reply("Газон от 590 ₽.") == ("Газон от 590 ₽.", False)
    assert clean_reply("<think>считаю…</think>Газон от 590 ₽ , сроки 3 дня .") == ("Газон от 590 ₽, сроки 3 дня.", False)


def test_free_visit_radius_is_unambiguous_in_kb():
    kb = (ROOT / "sites" / SITE / "kb.md").read_text(encoding="utf-8")
    assert "бесплатный выезд на замер — только до 50 км" in kb and "дальше 50 км" in kb


@pytest.mark.parametrize("raw,out", [("8 (921) 123-45-67", "+79211234567"), ("9211234567", "+79211234567"),
                                     ("+971 50 123 4567", "+971501234567")])
def test_phone(raw, out):
    assert normalize_phone(raw) == out


def test_rate_limiter():
    rl = RateLimiter(2)
    assert rl.allow("ip", 0) and rl.allow("ip", 1) and not rl.allow("ip", 2) and rl.allow("ip", 61.5)


def test_telegram_text_escapes_html():
    text = telegram_text("Сад <b>", {"name": "<script>", "phone": "+7", "page": "x", "lang": "ru"},
                         {"summary": "a & b"}, [{"role": "user", "content": "1 < 2"}])
    assert "&lt;script&gt;" in text and "a &amp; b" in text and "1 &lt; 2" in text and "Сад &lt;b&gt;" in text


# ---------- API ----------
def test_public_config_hides_private_fields(env):
    client, _ = make(env)
    cfg = client.get(f"/api/sites/{SITE}/config").json()
    assert cfg["title"] == "Зелёный контур" and cfg["accent"] == "#2f6b4f"
    flat = json.dumps(cfg, ensure_ascii=False)
    assert "-100500" not in flat and "crm.test" not in flat and "qualify" not in cfg and "Дренаж" not in flat


@pytest.mark.parametrize("site", ["nope", "..", "Zeleny-Kontur", "a"])
def test_unknown_or_bad_site(env, site):
    client, _ = make(env)
    assert client.get(f"/api/sites/{site}/config").status_code == 404


def test_origin_allow_list(env):
    cfg = env.sites_dir / SITE / "site.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["allowed_origins"] = ["https://zeleny-kontur.ru"]
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    client, _ = make(env)
    assert client.get(f"/api/sites/{SITE}/config", headers={"Origin": "https://zeleny-kontur.ru"}).status_code == 200
    assert client.post("/api/chat", json={**CHAT, "message": "газон"},
                       headers={"Origin": "https://evil.example"}).status_code == 403


def test_chat_answers_from_model_with_kb_and_history(env):
    calls = []
    client, _ = make(env, calls)
    first = client.post("/api/chat", json={**CHAT, "message": "Сколько стоит газон?"}).json()
    assert first["by"] == "ai" and "590" in first["reply"] and first["show_form"] is False
    system = calls[0]["messages"][0]["content"]
    assert "Зелёный контур" in system and "## Автополив" in system and "площадь участка в сотках" in system
    client.post("/api/chat", json={**CHAT, "message": "А сроки?"})
    assert [m["role"] for m in calls[1]["messages"]] == ["system", "user", "assistant", "user"]


def test_lead_marker_and_intent_open_the_form(env):
    client, _ = make(env)
    r = client.post("/api/chat", json={**CHAT, "message": "Рассчитайте мой участок 12 соток"}).json()
    assert r["show_form"] is True and "[[LEAD]]" not in r["reply"]
    r = client.post("/api/chat", json={**CHAT, "session": "test-session-0002",
                                       "message": "Сколько стоит газон? Перезвоните мне"}).json()
    assert r["show_form"] is True  # the model didn't add the marker, but the visitor asked for a call


def test_arabic_visitor(env):
    calls = []
    client, _ = make(env, calls)
    r = client.post("/api/chat", json={**CHAT, "message": "هل تقومون بتصميم الحدائق؟"}).json()
    assert r["lang"] == "ar" and "590" in r["reply"] and r["show_form"] is True
    system, question = calls[0]["messages"][0]["content"], calls[0]["messages"][-1]["content"]
    assert system.startswith("Отвечай только на арабском языке")
    assert "سوتكا" in system and "دونم" in system and "العشب الجاهز" in system   # the site's glossary
    assert question.endswith("(Ответь на арабском языке.)")


def test_russian_prompt_has_no_glossary(env):
    calls = []
    client, _ = make(env, calls)
    client.post("/api/chat", json={**CHAT, "message": "Сколько стоит газон?"})
    system = calls[0]["messages"][0]["content"]
    assert "سوتكا" not in system and calls[0]["messages"][-1]["content"] == "Сколько стоит газон?"


def test_yandex_project_header(env):
    import httpx
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Газон от 590 ₽."}}]})
    s = replace(env, llm_project="b1gfolder")
    client = TestClient(build_app(s, Consultant(s, httpx.AsyncClient(transport=httpx.MockTransport(handler))),
                                  Notifier("", "https://tg.test"), Store(s.db_path)))
    client.post("/api/chat", json={**CHAT, "message": "газон"})
    assert seen[0].headers["OpenAI-Project"] == "b1gfolder" and seen[0].headers["Authorization"] == "Api-Key k"


def test_model_failure_falls_back(env):
    client, _ = make(env, llm_fail=True)
    r = client.post("/api/chat", json={**CHAT, "message": "Сколько стоит автополив?"}).json()
    assert r["by"] == "fallback" and "85 000" in r["reply"] and r["show_form"] is True


def test_without_model_uses_knowledge_base(env):
    client, _ = make(replace(env, llm_url=""))
    r = client.post("/api/chat", json={**CHAT, "message": "Есть ли гарантия на мощение?"}).json()
    assert r["by"] == "kb" and "3 года" in r["reply"]


def test_session_message_cap(env):
    client, _ = make(replace(env, max_messages_per_session=2))
    for _ in range(2):
        client.post("/api/chat", json={**CHAT, "message": "газон"})
    r = client.post("/api/chat", json={**CHAT, "message": "газон"}).json()
    assert r["by"] == "limit" and r["show_form"] is True


def test_ip_rate_limit(env):
    client, _ = make(replace(env, max_requests_per_minute=3))
    codes = [client.post("/api/chat", json={**CHAT, "message": "газон"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_bad_input(env):
    client, _ = make(env)
    assert client.post("/api/chat", json={**CHAT, "session": "x"}).status_code == 422
    assert client.post("/api/chat", json={**CHAT, "message": ""}).status_code == 422
    assert client.post("/api/chat", json={**CHAT, "message": "a" * 1001}).status_code == 422


def test_lead_requires_consent_and_valid_phone(env):
    client, sent = make(env)
    assert client.post("/api/lead", json={**LEAD, "consent": False}).status_code == 422
    assert client.post("/api/lead", json={**LEAD, "phone": "12"}).status_code == 422
    assert client.post("/api/lead", json={**LEAD, "name": "А"}).status_code == 422
    assert sent == []


def test_lead_goes_to_telegram_and_webhook(env):
    client, sent = make(env)
    client.post("/api/chat", json={**CHAT, "message": "Хочу газон и автополив на 12 сотках во Всеволожске"})
    r = client.post("/api/lead", json={**LEAD, "comment": "Звонить после 18"}).json()
    assert r["ok"] and "15 минут" in r["message"]
    (tg_url, tg), (hook_url, hook) = sent
    assert tg_url == "https://tg.test/bot123:ABC/sendMessage" and tg["chat_id"] == "-100500"
    assert "Анна" in tg["text"] and "+79211234567" in tg["text"] and "12 сотках" in tg["text"]
    assert "Нужен расчёт газона и автополива" in tg["text"] and "Звонить после 18" in tg["text"]
    assert hook_url == "https://crm.test/hook" and hook["phone"] == "+79211234567" and hook["source"] == "ai-chat"
    assert hook["timeline"] == "весна 2027" and hook["dialog"][0]["role"] == "user"
    # a second click within 10 minutes doesn't ping the manager again
    assert client.post("/api/lead", json=LEAD).json()["ok"] and len(sent) == 2


def test_lead_saved_even_if_telegram_is_down(env):
    client, sent = make(env, tg_fail=True)
    assert client.post("/api/lead", json=LEAD).json()["ok"]
    stats = Store(env.db_path).stats(SITE, 7)
    assert stats["leads"] == 1


def test_embed_widget_and_demo(env):
    client, _ = make(env)
    assert client.get(f"/embed/{SITE}").text.strip() == \
        '<script src="https://ai.example.ru/widget.js" data-site="zeleny-kontur" defer></script>'
    w = client.get("/widget.js")
    assert w.status_code == 200 and "javascript" in w.headers["content-type"] and "attachShadow" in w.text
    assert "data-site=\"zeleny-kontur\"" in client.get("/demo/").text
    assert client.get("/", follow_redirects=False).headers["location"] == "/demo/"


def test_report_and_purge(env):
    client, _ = make(env)
    client.post("/api/chat", json={**CHAT, "message": "Сколько стоит газон?"})
    client.post("/api/chat", json={**CHAT, "message": "А сроки?"})
    client.post("/api/lead", json=LEAD)
    store = Store(env.db_path)
    st = store.stats(SITE, 7)
    assert (st["dialogs"], st["messages"], st["leads"], st["conversion_pct"]) == (1, 2, 1, 100.0)
    assert st["first_questions"] == ["Сколько стоит газон?"]
    assert "Заявок: 1" in render("Зелёный контур", st)
    assert store.purge(0) >= 4 and store.stats(SITE, 7)["dialogs"] == 0


def test_private_settings_file_overrides_and_stays_private(env):
    local = env.sites_dir / SITE / "site.local.json"
    local.write_text(json.dumps({"telegram_chat_id": "-200777", "lead_emails": "boss@example.ru"}), encoding="utf-8")
    client, sent = make(env)
    assert "-200777" not in client.get(f"/api/sites/{SITE}/config").text
    client.post("/api/lead", json=LEAD)
    assert sent[0][1]["chat_id"] == "-200777"


def test_lead_goes_to_max_and_email(env):
    import httpx
    local = env.sites_dir / SITE / "site.local.json"
    local.write_text(json.dumps({"max_chat_id": "-7001", "lead_emails": ["boss@example.ru", "not-an-email"]}),
                     encoding="utf-8")
    seen, mails = [], []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})
    notifier = Notifier(env.telegram_token, env.telegram_api, httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                        max_token="max-token", max_api="https://max.test",
                        smtp=Smtp("smtp.test", 465, "bot@example.ru", "app-pass"),
                        send_mail=lambda cfg, msg: mails.append((cfg, msg)))
    client = TestClient(build_app(env, Consultant(env, fake_llm()), notifier, Store(env.db_path)))
    client.post("/api/chat", json={**CHAT, "message": "Хочу газон на 12 сотках"})
    assert client.post("/api/lead", json=LEAD).json()["ok"]
    mx = next(r for r in seen if r.url.host == "max.test")
    assert mx.url.path == "/messages" and mx.url.params["chat_id"] == "-7001"
    assert mx.headers["Authorization"] == "max-token" and "access_token" not in str(mx.url)
    body = json.loads(mx.content)
    assert body["format"] == "html" and "Анна" in body["text"] and "+79211234567" in body["text"]
    (cfg, msg), = mails
    assert msg["To"] == "boss@example.ru" and msg["From"] == "bot@example.ru" and "Анна" in msg["Subject"]
    text = msg.get_body(("plain",)).get_content()
    assert "+79211234567" in text and "<b>" not in text
    assert "<br>" in msg.get_body(("html",)).get_content()
    led = Store(env.db_path).conn.execute("SELECT delivered FROM leads").fetchone()[0]
    assert json.loads(led) == {"telegram": True, "max": True, "email": True, "webhook": True}


def test_site_max_bot_overrides_server_bot(env):
    import httpx
    (env.sites_dir / SITE / "site.local.json").write_text(
        json.dumps({"max_user_id": "42", "max_token": "client-own-bot"}), encoding="utf-8")
    seen = []
    notifier = Notifier("", "https://tg.test", httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: seen.append(r) or httpx.Response(200, json={}))), max_token="server-bot", max_api="https://max.test")
    client = TestClient(build_app(env, Consultant(env, fake_llm()), notifier, Store(env.db_path)))
    client.post("/api/lead", json=LEAD)
    mx = next(r for r in seen if r.url.host == "max.test")
    assert mx.url.params["user_id"] == "42" and mx.headers["Authorization"] == "client-own-bot"


def test_one_channel_failing_keeps_the_others(env):
    import httpx
    (env.sites_dir / SITE / "site.local.json").write_text(json.dumps({"lead_emails": ["boss@example.ru"]}),
                                                         encoding="utf-8")
    sent = []

    def broken_smtp(cfg, msg):
        raise OSError("smtp down")
    notifier = Notifier(env.telegram_token, env.telegram_api, recorder(sent),
                        smtp=Smtp("smtp.test", 465, "bot@example.ru", "p"), send_mail=broken_smtp)
    client = TestClient(build_app(env, Consultant(env, fake_llm()), notifier, Store(env.db_path)))
    assert client.post("/api/lead", json=LEAD).json()["ok"]
    assert [u for u, _ in sent] == ["https://tg.test/bot123:ABC/sendMessage", "https://crm.test/hook"]
    led = json.loads(Store(env.db_path).conn.execute("SELECT delivered FROM leads").fetchone()[0])
    assert led == {"telegram": True, "max": False, "email": False, "webhook": True}


def test_health_shows_channels(env):
    client, _ = make(env)
    h = client.get("/health").json()
    assert h["ok"] and h["telegram"] and h["max"] is False and h["email"] is False and "version" in h


def test_plain_text_for_email():
    assert plain("<b>Заявка</b> &amp; <i>вопрос</i>") == "Заявка & вопрос"
