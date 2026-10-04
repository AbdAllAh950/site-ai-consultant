"""The widget in a real browser against a real server (model and Telegram are scripted stand-ins).
Needs: pip install playwright && playwright install chromium — skipped otherwise."""
import json
import shutil
import socket
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from app.config import ROOT, Settings  # noqa: E402
from app.consultant import Consultant  # noqa: E402
from app.main import build_app  # noqa: E402
from app.notify import Notifier  # noqa: E402
from app.store import Store  # noqa: E402
from tests.fakes import fake_llm, recorder  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    shutil.copytree(ROOT / "sites", tmp / "sites")
    cfg = tmp / "sites" / "zeleny-kontur" / "site.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["telegram_chat_id"] = "-100500"
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    s = Settings(llm_url="https://llm.test/v1/chat/completions", llm_auth="Api-Key k", llm_model="m",
                 telegram_token="123:ABC", telegram_api="https://tg.test", sites_dir=tmp / "sites",
                 db_path=tmp / "db.sqlite", max_requests_per_minute=1000)
    sent: list = []
    app = build_app(s, Consultant(s, fake_llm()), Notifier(s.telegram_token, s.telegram_api, recorder(sent)),
                    Store(s.db_path))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", sent
    srv.should_exit = True
    thread.join(5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        yield b
        b.close()


def open_chat(page, base):
    page.goto(base + "/demo/")
    page.wait_for_selector("#ai-consultant .launcher")
    page.locator("[data-open-chat]").first.click()
    page.wait_for_selector("#ai-consultant .panel:not([hidden])")


def test_question_form_and_lead_on_desktop(server, browser):
    base, sent = server
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    open_chat(page, base)
    assert "AI-консультант «Зелёного контура»" in page.locator(".msg.bot").first.inner_text()
    assert page.locator(".chip").count() == 4

    page.locator(".chip", has_text="Сколько стоит газон?").click()
    page.wait_for_function("(document.querySelector('#ai-consultant').shadowRoot.querySelectorAll('.msg.bot')[1] || {})"
                           ".textContent?.includes('590')")  # the reply bubble appears first, then fills
    assert "590 ₽" in page.locator(".msg.bot").nth(1).inner_text()
    assert page.locator(".chip").count() == 0 and page.locator(".card").count() == 0

    page.locator(".composer textarea").fill("Рассчитайте, пожалуйста, мой участок 12 соток")
    page.keyboard.press("Enter")
    page.wait_for_selector("#ai-consultant .card")
    card = page.locator(".card")
    card.locator("input[name=name]").fill("Анна")
    card.locator("input[name=phone]").fill("123")
    card.locator("button[type=submit]").click()
    assert card.locator(".err").inner_text() == "Проверьте номер телефона"
    card.locator("input[name=phone]").fill("+7 921 123-45-67")
    card.locator("button[type=submit]").click()
    assert card.locator(".err").inner_text() == "Нужно согласие на обработку данных"
    card.locator("input[name=consent]").check()
    card.locator("button[type=submit]").click()
    page.wait_for_selector("#ai-consultant .done")
    assert "15 минут" in page.locator(".done").inner_text()
    tg = [body for url, body in sent if url.endswith("/sendMessage")][-1]
    assert "Анна" in tg["text"] and "+79211234567" in tg["text"] and "12 соток" in tg["text"]

    page.reload()  # the conversation survives navigation within the visit
    page.wait_for_selector("#ai-consultant .launcher")
    page.locator("#ai-consultant .launcher").click()
    assert page.locator(".msg.user").count() == 2 and page.locator(".done").count() == 1
    assert page.locator(".cta").is_hidden()
    page.close()


def test_arabic_conversation_switches_to_rtl(server, browser):
    base, _ = server
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    open_chat(page, base)
    page.locator(".composer textarea").fill("هل تقومون بتصميم الحدائق؟")
    page.locator(".send").click()
    page.wait_for_selector("#ai-consultant .card")
    assert page.locator(".panel").get_attribute("dir") == "rtl"
    assert "590" in page.locator(".msg.bot").nth(1).inner_text()
    assert page.locator(".card p").inner_text() == "اترك بياناتك وسيتصل بك المدير خلال ١٥ دقيقة"
    page.close()


def test_phone_layout_is_full_screen(server, browser):
    base, _ = server
    page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page.goto(base + "/demo/")
    page.wait_for_selector("#ai-consultant .launcher")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.locator("#ai-consultant .launcher").click()
    page.wait_for_timeout(400)  # let the opening animation finish
    box = page.locator(".panel").bounding_box()
    assert round(box["width"]) == 390 and round(box["height"]) == 844 and box["x"] == 0 and round(box["y"]) == 0
    assert page.locator(".launcher").is_hidden()
    page.locator(".icon").click()   # close returns the launcher
    assert page.locator(".launcher").is_visible()
    page.close()


def test_host_page_styles_cannot_break_widget(server, browser):
    base, _ = server
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    page.goto(base + "/demo/")
    page.add_style_tag(content=".panel,.msg,button,textarea{display:none!important;color:red!important}")
    page.wait_for_selector("#ai-consultant .launcher")
    page.locator("#ai-consultant .launcher").click()
    assert page.locator(".panel").is_visible() and page.locator(".composer textarea").is_visible()
    page.close()
