# AI chat consultant for websites · AI-консультант для сайта

🇬🇧 [English](#english) · 🇷🇺 [Русский](#русский)

An LLM agent that answers a business's website visitors from its own knowledge base, decides when to offer a lead form, and delivers a summarized lead to Telegram, MAX, email or any CRM. FastAPI, OpenAI-compatible LLM API (YandexGPT), Docker, 78 tests.

**Engineering highlights**

| | |
|---|---|
| 🔭 **Tracing** — OpenTelemetry, GenAI semantic conventions | [`app/tracing.py`](app/tracing.py): `invoke_agent` → `retrieval` → `chat <model>` spans with provider, model, tokens, finish reason, latency, `error.type`; visitor text kept out of traces by default |
| 🧪 **Evals** — 20 cases in Russian, English, Arabic | [`app/evals.py`](app/evals.py), [`evals/`](evals/zeleny-kontur.json): retrieval hit@1/hit@3/MRR per language; live behaviour checks incl. **grounding** (every number in a reply must exist in the knowledge base); re-run automatically after each behaviour-changing deploy |
| 🔎 **What evals found** | keyword retrieval: hit@3 = 100% for Russian questions, 0% for English/Arabic (Russian knowledge base) → fallback for large bases; multilingual embeddings next |
| 🔌 **MCP server** | [`app/mcp_server.py`](app/mcp_server.py): read-only tools (`list_sites`, `search_knowledge`, `ask_consultant`, `evaluate_retrieval`) + `kb://{site}` resource, least privilege, tested via a real MCP client session |
| ✅ **Tests** | [`tests/`](tests): API, tracing, evals, MCP, and Playwright end-to-end runs against the real server |

![Демо: ландшафтное бюро с AI-консультантом](assets/demo-desktop.png)

---

## Русский

Чат на сайте, который круглосуточно отвечает посетителям по информации компании, уточняет задачу и отдаёт менеджеру готовую заявку в Telegram или CRM. Ставится на Tilda и любой сайт одной строкой кода. Один сервер обслуживает сколько угодно сайтов клиентов.

**Что умеет**

- 💬 **Отвечает по базе знаний компании** (YandexGPT): услуги, цены «от», сроки, гарантии. Чего нет в базе — не выдумывает, а предлагает связаться с менеджером.
- 🎯 **Ловит момент заявки.** Когда посетитель просит расчёт, выезд или звонок, в чате появляется короткая форма: имя, телефон, согласие на обработку данных.
- 📨 **Заявка со сводкой** уходит менеджеру в Telegram, MAX и на почту: что нужно, детали, бюджет, сроки и последние вопросы клиента. Параллельно — JSON на вебхук (amoCRM, Bitrix24, n8n, Albato). Каналы независимы: если один недоступен, остальные всё равно доставят заявку.
- ⚙️ **n8n в комплекте:** готовый сценарий — заявка → оценка YandexGPT (горячая / тёплая / холодная и что делать менеджеру) → строка в Google Таблице → срочное сообщение по горячим.
- 🌍 **Русский, английский, арабский** — язык определяется по сообщению, для арабского окно чата зеркалится (RTL).
- 🧩 **Не ломает сайт клиента:** виджет живёт в Shadow DOM, стили сайта на него не влияют и наоборот. Цвет и тексты задаются в настройках, шрифт берётся с сайта.
- 🛡 **Готов к продакшену:** список разрешённых доменов для каждого сайта, лимиты сообщений против накрутки расходов на нейросеть, переписка удаляется через 90 дней (152-ФЗ), данные и модель в России.
- 📊 **Отчёт раз в неделю:** диалоги, заявки, конверсия и с чего начинают разговор.
- 🔁 **Если нейросеть недоступна**, ответ берётся из базы знаний, а форма заявки открывается сразу — заявка не теряется.

![Телефон: русский и арабский](assets/demo-phone.png)

### Как подключить нового клиента (≈ 1 час)

1. Скопируйте `sites/zeleny-kontur` в `sites/<id-клиента>` (латиница, цифры, дефис).
2. **`kb.md`** — база знаний: первый абзац о компании, дальше разделы `## Заголовок` с фактами (услуги и цены, сроки, география, оплата, гарантии, частые вопросы).
3. **`site.json`** — название, цвет (`accent`), приветствие, подсказки-кнопки, тексты формы, ссылка на политику конфиденциальности, `qualify` (что узнать до формы), `allowed_origins` (домены сайта клиента), `glossary` (как переводить термины клиента на арабский и английский).
   Куда слать заявки — в **`site.local.json`** рядом (он не попадает в git): `telegram_chat_id`, `max_chat_id` или `max_user_id` (+ `max_token`, если у клиента свой бот в MAX), `lead_emails`, `webhook_url`.
4. Добавьте вашего Telegram-бота в чат менеджеров клиента и узнайте id чата: напишите в чат любое сообщение и откройте `https://api.telegram.org/bot<ТОКЕН>/getUpdates` — нужное поле `chat.id` (у групп начинается с `-100`).
5. Код для сайта: `https://<ваш-сервер>/embed/<id-клиента>` покажет готовую строку:
   ```html
   <script src="https://ai.example.ru/widget.js" data-site="id-клиента" defer></script>
   ```
   **Tilda:** Настройки сайта → Вставка кода → «HTML-код для вставки внутрь head» → сохранить → переопубликовать все страницы ([справка Tilda](https://tilda.cc/ru/answers/a/html-in-head/)).

Перезапуск сервера не нужен: изменения в `kb.md` и `site.json` подхватываются на следующем сообщении.

### Нейросеть: YandexGPT

1. В [консоли Yandex Cloud](https://console.yandex.cloud) создайте платёжный аккаунт (карта РФ) и каталог; скопируйте ID каталога.
2. Создайте сервисный аккаунт с ролью `ai.languageModels.user` и API-ключ для него.
3. В `.env`:
   ```
   LLM_URL=https://ai.api.cloud.yandex.net/v1/chat/completions
   LLM_AUTH=Api-Key <ключ>
   LLM_MODEL=gpt://<id-каталога>/yandexgpt/latest
   LLM_SUMMARY_MODEL=gpt://<id-каталога>/yandexgpt-lite/latest
   LLM_PROJECT=<id-каталога>
   ```
Подойдёт и любой другой OpenAI-совместимый API (GigaChat через совместимый шлюз и т. п.). Сводки для менеджера считает дешёвая Lite-модель.

### Сервер

Любой VPS в России (Timeweb Cloud, Selectel, Yandex Cloud) на Ubuntu с Docker:

```bash
git clone https://github.com/AbdAllAh950/site-ai-consultant.git && cd site-ai-consultant
cp .env.example .env      # ключи, токен бота и DOMAIN
docker compose up -d --build
```

HTTPS выпускается автоматически (Caddy + Let's Encrypt). Нет домена — укажите `DOMAIN=<IP-сервера-через-дефисы>.sslip.io`, например `203-0-113-5.sslip.io`. Демо будет по адресу `https://<домен>/demo/`.

Отчёт для клиента: `docker compose exec app python -m app.report <id-клиента> --send`. Еженедельно — через cron.

**Каналы заявок.** Почта: `SMTP_HOST/PORT/USER/PASSWORD/FROM` в `.env` (Яндекс: `smtp.yandex.ru:465` и пароль приложения) или команда `ai-email-setup` на сервере. MAX: бот создаётся на платформе MAX для партнёров (нужен верифицированный профиль юрлица, ИП или самозанятого), токен — в `MAX_BOT_TOKEN` или `max_token` клиента. Проверить все каналы сайта: `docker compose exec app python -m app.notify test <id-клиента>`.

**n8n** поднимается вместе с сервисом: `https://n8n.<домен>/`. Сценарий `n8n/ai-lead-to-sheet.json` импортируется сам; в нём нужно выбрать доступ к Google (сервисный аккаунт) и включить сценарий. Чтобы заявки шли в n8n, в `site.local.json` укажите `"webhook_url": "http://n8n:5678/webhook/ai-lead"`.

**Автодеплой.** `deploy/bootstrap.sh` один раз включает таймер: сервер каждую минуту проверяет `main` на GitHub и сам пересобирается при изменениях (`sudo ai-deploy` — сразу). Версия видна в `/health`.

### Локальный запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # без ключа нейросети чат отвечает из базы знаний
uvicorn app.main:app --reload
open http://localhost:8000/demo/
```

### Трейсинг, eval и MCP

- **Трейсинг (OpenTelemetry, семантические соглашения GenAI).** Одно сообщение посетителя — один трейс: `invoke_agent` → `retrieval` (какие разделы базы, оценки) → `chat <модель>` (провайдер, модель, токены, причина остановки, задержка, `error.type`). Заявка — спан `lead` и по спану на каждый канал доставки. Тексты посетителей и промпты в трейсы не пишутся, пока не включено `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. Экспорт в любой OTLP-бэкенд — `OTEL_EXPORTER_OTLP_ENDPOINT`; `/health` всегда показывает вызовы модели за час: количество, ошибки, p50/p95, токены.
- **Eval (`evals/<site>.json`).** 20 вопросов на трёх языках. Офлайн: качество поиска по базе (hit@1, hit@3, MRR по языкам) — в тестах. С моделью: каждый вопрос проходит через настоящий промпт и модель, ответ проверяется правилами — нужные факты, форма заявки, язык ответа, без markdown, без утечки промпта и **без выдуманных чисел**: каждое число в ответе должно быть в базе знаний или в вопросе. После деплоя сервер сам прогоняет eval один раз на каждое изменение промпта, базы, настроек сайта, модели или вопросов и присылает итог в Telegram. `python -m app.evals <site> [--live]`.
- **MCP-сервер (`python -m app.mcp_server`).** Инструменты для AI-ассистентов и агентов (Claude Desktop, Cursor): `list_sites`, `search_knowledge`, `ask_consultant`, `evaluate_retrieval` и ресурс `kb://{site}`. Только чтение: без доступа к перепискам и заявкам, входные данные проверяются.

### Тесты

```bash
pip install -r requirements-mcp.txt playwright && playwright install chromium
pytest -q
```

78 тестов. Трейсинг: структура трейса и атрибуты GenAI, токены, ошибки модели, отсутствие текстов посетителей в трейсах по умолчанию, спаны доставки заявки. Eval: проверки ответов (выдуманные числа, язык, markdown, форма), прогон через консультанта, повторный запуск только при изменениях. MCP: настоящая клиентская сессия, ошибки ввода. Остальное: API: база знаний, язык и глоссарий, ответы модели с историей, метка заявки и намерение «перезвоните», падение модели, работа без модели, лимиты, домены, согласие и телефон, доставка в Telegram, MAX, на почту и на вебхук (сводка в ```-блоке), отказ одного канала, приватные настройки сайта, повторная заявка, экранирование HTML, отчёт и удаление старых данных. Браузер (Playwright, реальный сервер): вопрос → ответ → форма → ошибки → заявка → перезагрузка страницы, арабский RTL, полноэкранный чат на телефоне, стили сайта не ломают виджет.

### Структура

```
app/main.py          API: /api/chat, /api/lead, /api/sites/{id}/config, /widget.js, /embed/{id}, /demo
app/consultant.py    промпт, ответы модели, метка заявки, сводка для менеджера
app/knowledge.py     база знаний из Markdown, поиск по разделам
app/tracing.py       OpenTelemetry: трейсы по соглашениям GenAI, сводка вызовов модели для /health
app/evals.py         eval: качество поиска и поведение консультанта, запуск после деплоя
app/mcp_server.py    MCP-сервер: инструменты только для чтения для AI-агентов
evals/<id>.json      вопросы для eval по каждому сайту
app/store.py         SQLite: переписка, заявки, статистика, удаление старых данных
app/notify.py        Telegram, MAX, почта и вебхук; проверка каналов
n8n/                 сценарии n8n (заявка → YandexGPT → Google Таблица)
deploy/              Caddy, автодеплой с GitHub, настройка ключей и почты на сервере
app/report.py        еженедельный отчёт
widget/widget.js     виджет (без зависимостей, ~22 КБ)
sites/<id>/          настройки и база знаний каждого клиента
demo/                демо-сайт вымышленного ландшафтного бюро
scripts/make_plans.py  генератор иллюстраций-генпланов для демо
```

---

## English

An AI chat for business websites: it answers visitors 24/7 from the company's own knowledge base (YandexGPT, no invented prices), notices when someone wants a quote, a site visit or a call, collects name, phone and consent right in the chat, and sends the manager a summarized lead in Telegram, MAX and email, plus a JSON webhook for any CRM. A bundled n8n workflow scores each lead with YandexGPT and appends it to a Google Sheet. It installs on Tilda or any site with one `<script>` tag, and one server hosts many client sites.

It speaks Russian, English and Arabic: the language is detected per message and the window mirrors for RTL. The widget is a dependency-free Shadow DOM component, so the host page's CSS can't break it. Each site has its own allowed domains. There are per-visitor and per-session limits on model spend, a 90-day retention purge, and Russian hosting for 152-FZ. If the model is down, the chat falls back to the knowledge base and shows the lead form, so no lead is lost. A weekly report goes to the client's Telegram.

### Observability, evals and MCP

- **Tracing — OpenTelemetry, GenAI semantic conventions** (`app/tracing.py`). One visitor message is one trace: `invoke_agent` → `retrieval` (which knowledge-base sections, scores, whole or narrowed context) → `chat <model>` (provider, model, input/output tokens, finish reason, latency, `error.type`). A lead is a `lead` span with one `deliver_lead <channel>` child per channel. Visitors' text and prompts stay out of traces unless `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. `OTEL_EXPORTER_OTLP_ENDPOINT` sends spans to any OTLP backend; `/health` always shows the last hour of model calls: count, errors, p50/p95 latency, tokens.
- **Evals** (`evals/<site>.json`, `app/evals.py`). 20 cases in Russian, English and Arabic. *Offline:* knowledge-base search hit@1 / hit@3 / MRR per language, run with the test suite. *Live:* each case goes through the real prompt, model and lead logic, and the reply is checked by rules — expected facts, lead form offered or not, reply language, no markdown, no system-prompt leakage, and **grounding**: every number in a reply must exist in the knowledge base or in the question, which catches invented prices and discounts. After each deploy the server runs the live evals once per change to the prompt, knowledge base, site settings, model or cases (a fingerprint of all of them), reports to Telegram, shows the result in `/health` and emits `gen_ai.evaluation.result` span events. `python -m app.evals <site> [--live]`.
- **What the evals found.** Keyword search puts the right section in the top 3 for 100% of Russian questions and 0% of English or Arabic ones — the knowledge base is Russian. Small bases are sent to the model whole, so replies were unaffected, but on a large base foreign-language visitors would have got the intro only. Such queries now get as many sections as fit; multilingual embeddings are the next step.
- **MCP server** (`app/mcp_server.py`, `python -m app.mcp_server`). Read-only tools for assistants and agents (Claude Desktop, Cursor, your own): `list_sites`, `search_knowledge`, `ask_consultant`, `evaluate_retrieval`, and a `kb://{site}` resource. Least privilege: no access to dialogs or leads, no side effects, validated and length-limited inputs, errors returned as tool errors. Tested through a real MCP client session.

**Run:** `pip install -r requirements.txt && uvicorn app.main:app` → `http://localhost:8000/demo/`. **Deploy:** `docker compose up -d --build` (Caddy issues HTTPS automatically). **Tests:** `pip install -r requirements-mcp.txt && pytest -q`: 78 tests, including Playwright end-to-end runs against the real server and an MCP client session.

The demo company "Zeleny Kontur" (a landscape studio), its prices and contacts are fictional.

---

Автор / Author: Abdallah Essa · MSc Big Data & ML, ITMO · [GitHub](https://github.com/AbdAllAh950)
