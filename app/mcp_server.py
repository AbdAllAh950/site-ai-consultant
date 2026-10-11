"""MCP server: an AI assistant or agent (Claude Desktop, Cursor, your own agent) works with the consultant's sites.

Tools
  list_sites            sites on this server
  search_knowledge      ranked sections of a site's knowledge base for a query
  ask_consultant        the consultant's reply to a visitor question (same prompt, model and lead logic as the widget)
  evaluate_retrieval    hit@1 / hit@3 / MRR of the knowledge-base search on the site's eval cases
Resources
  kb://{site}           a site's knowledge base (Markdown)

Least privilege: every tool is read-only. The server never sees visitors' dialogs or leads,
cannot send messages and cannot change sites; inputs are length-limited and site ids validated.

Run (stdio):  pip install -r requirements-mcp.txt && python -m app.mcp_server
Claude Desktop → claude_desktop_config.json:
  {"mcpServers": {"site-consultant": {"command": "python", "args": ["-m", "app.mcp_server"],
                                      "cwd": "/path/to/site-ai-consultant"}}}
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import evals
from .config import Settings, Site, SiteRegistry
from .consultant import Consultant
from .knowledge import KnowledgeBase

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
MAX_QUERY = 500


def build(settings: Settings | None = None, consultant: Consultant | None = None) -> FastMCP:
    s = settings or Settings.from_env()
    sites = SiteRegistry(s.sites_dir)
    consultant = consultant or Consultant(s)
    mcp = FastMCP("site-ai-consultant", instructions=(
        "Tools for an AI sales consultant that answers website visitors from each site's knowledge base. "
        "Start with list_sites. Use search_knowledge to see what the knowledge base says, ask_consultant to see "
        "exactly what a visitor would be told, evaluate_retrieval to measure search quality."))

    def site_or_error(site_id: str) -> Site:
        site = sites.get(site_id)
        if not site:
            known = ", ".join(sorted(p.name for p in s.sites_dir.iterdir() if (p / "site.json").exists()))
            raise ValueError(f"unknown site {site_id!r}; known sites: {known}")
        return site

    def text_arg(name: str, value: str, limit: int = MAX_QUERY) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError(f"{name} is empty")
        if len(value) > limit:
            raise ValueError(f"{name} is longer than {limit} characters")
        return value

    @mcp.tool(annotations=READ_ONLY)
    def list_sites() -> list[dict]:
        """Sites served by this consultant: id, company name, what it does, knowledge-base sections."""
        out = []
        for folder in sorted(p for p in s.sites_dir.iterdir() if p.is_dir()):
            site = sites.get(folder.name)
            if site:
                out.append({"id": site.id, "name": site.name, "about": site.about,
                            "sections": [c.title for c in KnowledgeBase(site.kb_text).chunks]})
        return out

    @mcp.tool(annotations=READ_ONLY)
    def search_knowledge(site: str, query: str, k: int = 3) -> list[dict]:
        """Knowledge-base sections of `site` that best match `query` (keyword search, Russian bases).

        Returns up to k sections (1–10) with a relevance score; an empty list means no section matched."""
        kb = KnowledgeBase(site_or_error(site).kb_text)
        k = max(1, min(int(k), 10))
        return [{"section": c.title, "score": round(score, 3), "text": c.body}
                for score, c in kb.search(text_arg("query", query), k=k)]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False,
                                          openWorldHint=True))
    async def ask_consultant(site: str, question: str) -> dict:
        """What the site's consultant would answer a visitor (one question, no prior dialog).

        show_form tells whether the lead form would open; by is "ai" (model), "kb" (no model configured)
        or "fallback" (model failed). Nothing is stored and no lead is sent."""
        target = site_or_error(site)
        reply = await consultant.answer(target, KnowledgeBase(target.kb_text), [],
                                        text_arg("question", question, 1000), conversation="mcp")
        return {"reply": reply.text, "show_form": reply.show_form, "lang": reply.lang, "by": reply.by}

    @mcp.tool(annotations=READ_ONLY)
    def evaluate_retrieval(site: str) -> dict:
        """Search quality on the site's eval cases (evals/<site>.json): hit@1, hit@3, MRR overall and per language,
        plus the cases where the expected section was not ranked first."""
        target = site_or_error(site)
        cases = evals.load_cases(target.id)
        if not cases:
            raise ValueError(f"no eval cases for {target.id!r} (evals/{target.id}.json)")
        report = evals.retrieval_report(KnowledgeBase(target.kb_text), cases)
        report["cases"] = [r for r in report["cases"] if r["rank"] != 1]
        return report

    @mcp.resource("kb://{site}", mime_type="text/markdown")
    def knowledge_base(site: str) -> str:
        """The site's whole knowledge base in Markdown."""
        return site_or_error(site).kb_text

    return mcp


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    build().run()          # stdio


if __name__ == "__main__":
    main()
