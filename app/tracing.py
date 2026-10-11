"""Tracing with OpenTelemetry, following the GenAI semantic conventions.

One visitor message is one trace:

    invoke_agent <site>        the consultant answering (reply source, lead form shown or not)
      retrieval <site>         knowledge-base selection: whole base or narrowed, top sections, scores
      chat <model>             the LLM call: provider, model, tokens, finish reason, latency, error.type
    lead <site>                a lead from the form…
      deliver_lead <channel>   …one span per channel (Telegram, MAX, email, webhook), delivered or not

Message content (prompts, replies, visitor text) is not recorded unless
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true — visitors' words stay out of the traces by default.

Where spans go:
  OTEL_EXPORTER_OTLP_ENDPOINT=…   any OTLP backend (Jaeger, Grafana Tempo, Langfuse, Phoenix…);
                                  needs `pip install opentelemetry-exporter-otlp-proto-http`
  OTEL_TRACES_CONSOLE=1           stdout (debugging)
  always                          a small in-memory window of recent LLM calls → /health "llm"
                                  (calls, errors, p50/p95 latency, tokens in the last hour)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, SpanKind

log = logging.getLogger(__name__)
tracer = trace.get_tracer("site-ai-consultant")

# GenAI semantic-convention names (kept as strings: the Python constants are still "incubating")
OP, PROVIDER, REQ_MODEL = "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model"
RESP_MODEL, RESP_ID, FINISH = "gen_ai.response.model", "gen_ai.response.id", "gen_ai.response.finish_reasons"
IN_TOKENS, OUT_TOKENS = "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"

PROVIDERS = (("yandex", "yandex"), ("openai.com", "openai"), ("anthropic", "anthropic"), ("deepseek", "deepseek"),
             ("groq", "groq"), ("mistral", "mistral_ai"), ("openrouter", "openrouter"), ("gigachat", "gigachat"))


def capture_content() -> bool:
    return os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "").lower() == "true"


def provider_name(url: str) -> str:
    host = urlparse(url).hostname or ""
    return next((name for key, name in PROVIDERS if key in host), host or "unknown")


def model_label(model: str) -> str:
    """Yandex model URIs carry the cloud folder id (gpt://<folder>/yandexgpt/latest): keep only the model part."""
    return model.split("/", 3)[3] if model.startswith("gpt://") and model.count("/") >= 3 else model


def messages_json(messages: list[dict]) -> str:
    """Chat messages in the semantic-convention shape: role + text parts."""
    return json.dumps([{"role": m["role"], "parts": [{"type": "text", "content": m["content"]}]} for m in messages],
                      ensure_ascii=False)


class RecentLLMCalls(SpanProcessor):
    """Keeps numbers (never content) of the last finished `chat` spans, for /health."""

    def __init__(self, size: int = 1000):
        self.rows: deque[tuple[float, float, bool, int, int]] = deque(maxlen=size)
        self.lock = threading.Lock()

    def on_end(self, span: ReadableSpan) -> None:
        a = span.attributes or {}
        if a.get(OP) != "chat" or span.start_time is None or span.end_time is None:
            return
        row = (span.end_time / 1e9, (span.end_time - span.start_time) / 1e6, not span.status.is_ok,
               int(a.get(IN_TOKENS, 0) or 0), int(a.get(OUT_TOKENS, 0) or 0))
        with self.lock:
            self.rows.append(row)

    def summary(self, window_s: int = 3600) -> dict:
        cutoff = time.time() - window_s
        with self.lock:
            rows = [r for r in self.rows if r[0] >= cutoff]
        if not rows:
            return {"calls_1h": 0}
        latency = sorted(r[1] for r in rows)

        def pct(p: float) -> int:
            return round(latency[min(len(latency) - 1, int(p * len(latency)))])

        return {"calls_1h": len(rows), "errors_1h": sum(r[2] for r in rows), "p50_ms": pct(0.5), "p95_ms": pct(0.95),
                "input_tokens_1h": sum(r[3] for r in rows), "output_tokens_1h": sum(r[4] for r in rows)}


RECENT = RecentLLMCalls()
_lock = threading.Lock()
_configured = False


def setup() -> None:
    """Installs the tracer provider once per process (build_app may run many times in tests)."""
    global _configured
    with _lock:
        if _configured:
            return
        provider = TracerProvider(resource=Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "site-ai-consultant"),
            "service.version": os.getenv("APP_VERSION", "dev")}))
        provider.add_span_processor(RECENT)
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            except ImportError:
                log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set, but opentelemetry-exporter-otlp-proto-http "
                            "is not installed — spans are not exported")
        if os.getenv("OTEL_TRACES_CONSOLE") == "1":
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _configured = True


def error_type(exc: BaseException) -> str:
    """`error.type`: the HTTP status for HTTP errors (e.g. "429"), otherwise the exception class."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return str(code) if code else type(exc).__name__


@contextmanager
def chat_span(url: str, model: str, max_tokens: int, temperature: float,
              messages: list[dict]) -> Iterator[Span]:
    label = model_label(model)
    host = urlparse(url)
    with tracer.start_as_current_span(f"chat {label}", kind=SpanKind.CLIENT, attributes={
            OP: "chat", PROVIDER: provider_name(url), REQ_MODEL: label,
            "gen_ai.request.max_tokens": max_tokens, "gen_ai.request.temperature": temperature,
            "server.address": host.hostname or "", "server.port": host.port or 443}) as span:
        if capture_content():
            span.set_attribute("gen_ai.input.messages", messages_json(messages))
        try:
            yield span
        except Exception as exc:
            span.set_attribute("error.type", error_type(exc))
            raise


def record_chat_response(span: Span, data: dict, content: str) -> None:
    """Response attributes from an OpenAI-compatible answer (Yandex, OpenAI, DeepSeek…)."""
    usage = data.get("usage") or {}
    attrs = {RESP_ID: data.get("id"), RESP_MODEL: model_label(str(data.get("model") or "")) or None,
             IN_TOKENS: _int(usage.get("prompt_tokens") or usage.get("input_tokens")),
             OUT_TOKENS: _int(usage.get("completion_tokens") or usage.get("output_tokens"))}
    reasons = [str(c.get("finish_reason")) for c in data.get("choices") or [] if c.get("finish_reason")]
    if reasons:
        attrs[FINISH] = reasons
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)
    if capture_content():
        span.set_attribute("gen_ai.output.messages", messages_json([{"role": "assistant", "content": content}]))


def _int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@contextmanager
def agent_span(site_id: str, conversation: str = "") -> Iterator[Span]:
    attrs = {OP: "invoke_agent", "gen_ai.agent.name": f"consultant:{site_id}"}
    if conversation:
        attrs["gen_ai.conversation.id"] = conversation
    with tracer.start_as_current_span(f"invoke_agent consultant:{site_id}", kind=SpanKind.INTERNAL,
                                      attributes=attrs) as span:
        yield span


@contextmanager
def retrieval_span(site_id: str, query: str) -> Iterator[Span]:
    with tracer.start_as_current_span(f"retrieval kb:{site_id}", kind=SpanKind.INTERNAL, attributes={
            OP: "retrieval", "gen_ai.data_source.id": f"kb:{site_id}"}) as span:
        if capture_content():
            span.set_attribute("gen_ai.retrieval.query.text", query)
        yield span


def health() -> dict:
    return RECENT.summary()
