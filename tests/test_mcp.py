"""The MCP server, exercised through a real MCP client session (in memory, no network)."""
import asyncio
import json
import shutil

import pytest

pytest.importorskip("mcp")
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from app.config import ROOT, Settings  # noqa: E402
from app.consultant import Consultant  # noqa: E402
from app.mcp_server import build  # noqa: E402
from tests.fakes import fake_llm  # noqa: E402

SITE = "zeleny-kontur"


@pytest.fixture
def server(tmp_path):
    sites = tmp_path / "sites"
    shutil.copytree(ROOT / "sites", sites)
    s = Settings(llm_url="https://llm.test/v1/chat/completions", llm_auth="Api-Key k", llm_model="m",
                 sites_dir=sites, db_path=tmp_path / "db.sqlite")
    return build(s, Consultant(s, fake_llm()))


def session(server, steps):
    async def run():
        async with create_connected_server_and_client_session(server) as client:
            return await steps(client)
    return asyncio.run(run())


def payload(result):
    """A tool's return value: structured output when the SDK gives it, else the JSON text."""
    assert not result.isError, result.content
    if result.structuredContent is not None:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


def test_tools_are_listed_read_only(server):
    tools = session(server, lambda c: c.list_tools()).tools
    names = {t.name for t in tools}
    assert names == {"list_sites", "search_knowledge", "ask_consultant", "evaluate_retrieval"}
    assert all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools)


def test_list_and_search(server):
    async def steps(c):
        return (await c.call_tool("list_sites", {}),
                await c.call_tool("search_knowledge", {"site": SITE, "query": "сколько стоит рулонный газон", "k": 2}))
    sites, hits = session(server, steps)
    site, = payload(sites)
    assert site["id"] == SITE and "Газон" in site["sections"]
    hits = payload(hits)
    assert hits[0]["section"] == "Газон" and "590" in hits[0]["text"] and len(hits) <= 2


def test_ask_consultant_and_evaluate(server):
    async def steps(c):
        return (await c.call_tool("ask_consultant", {"site": SITE, "question": "Хочу расчёт участка"}),
                await c.call_tool("evaluate_retrieval", {"site": SITE}))
    answer, report = session(server, steps)
    answer = payload(answer)
    assert answer["by"] == "ai" and answer["show_form"] is True
    report = payload(report)
    assert report["by_lang"]["ru"]["hit@3"] >= 0.9


def test_bad_input_is_a_tool_error_not_a_crash(server):
    async def steps(c):
        return (await c.call_tool("search_knowledge", {"site": "../etc", "query": "x"}),
                await c.call_tool("search_knowledge", {"site": SITE, "query": "a" * 600}))
    unknown, too_long = session(server, steps)
    assert unknown.isError and "unknown site" in unknown.content[0].text and SITE in unknown.content[0].text
    assert too_long.isError and "longer than 500" in too_long.content[0].text


def test_knowledge_base_resource(server):
    async def steps(c):
        return await c.read_resource(f"kb://{SITE}")
    result = session(server, steps)
    assert result.contents[0].text.startswith("# Зелёный контур")
