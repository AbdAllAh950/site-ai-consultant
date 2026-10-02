"""Test doubles: a scripted OpenAI-compatible model and a Telegram/webhook recorder (no network)."""
from __future__ import annotations

import json
import re

import httpx

ANSWERS = [  # (pattern in the visitor's message, answer) — answers paraphrase the demo knowledge base
    (r"газон", "Рулонный газон под ключ — от 590 ₽ за м², с подготовкой основания и грунтом. Участок 6 соток "
               "укладываем за 3–5 дней. Какая у вас площадь?"),
    (r"проект|дизайн", "Эскиз-концепция — от 15 000 ₽ для участка до 10 соток, готов за 7 рабочих дней. Полный "
                       "дизайн-проект с 3D — от 2 500 ₽ за сотку. Проект можно заказать отдельно от работ."),
    (r"срок", "Участок 10 соток под ключ обычно занимает 3–6 недель после согласования проекта. Летом очередь "
              "на старт 1–3 недели."),
    (r"полив", "Автополив — от 85 000 ₽ для участка до 6 соток, с контроллером и датчиком дождя."),
]


def fake_llm(calls: list | None = None, fail: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        if fail:
            return httpx.Response(500, json={"error": "quota"})
        system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
        if system.startswith("По переписке"):
            card = {"service": "благоустройство участка под ключ", "details": "12 соток, Всеволожский район",
                    "budget": "", "timeline": "весна 2027", "summary": "Нужен расчёт газона и автополива на 12 сотках"}
            return reply("```json\n" + json.dumps(card, ensure_ascii=False) + "\n```")
        if re.search(r"[؀-ۿ]", user):
            return reply("نعم، نقدم تصميم الحدائق والعشب والري الآلي في سانت بطرسبرغ. تبدأ تكلفة العشب من 590 روبل "
                         "للمتر المربع. هل تريد حساب تكلفة أرضك؟ [[LEAD]]")
        if re.search(r"рассчит|расчёт|посчит|участ", user, re.I):
            return reply("Посчитаем точно после бесплатного выезда на участок. Оставьте контакты — менеджер "
                         "перезвонит и договорится о замере. [[LEAD]]")
        for pattern, answer in ANSWERS:
            if re.search(pattern, user, re.I):
                return reply(answer)
        return reply("Это уточнит менеджер. Оставьте контакты, и он свяжется с вами. [[LEAD]]")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}]})


def recorder(sent: list, fail: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((str(request.url), json.loads(request.content)))
        if fail:
            return httpx.Response(502, json={"ok": False})
        return httpx.Response(200, json={"ok": True})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
