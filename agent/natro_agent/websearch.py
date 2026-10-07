"""Web search with Tavily (free plan: 1,000 searches a month, no card): https://tavily.com

Offered only when TAVILY_API_KEY is set. Not personal, so the free Gemini tier
may use it too. Each search is one credit ("basic" depth); results are kept
short to keep requests small.
"""
import asyncio
import json
import os
import urllib.request

from natro_agent.tools import Tool, ToolError

URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
SNIPPET_CHARS = 500
TIMEOUT_SECONDS = 20


def post(url, body, key):
    """POST JSON, return the decoded JSON reply (blocking)."""
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def format_results(data):
    """Tavily's reply as compact text for the model."""
    lines = []
    if data.get("answer"):
        lines.append(f"Summary: {data['answer']}")
    for number, result in enumerate(data.get("results", []), 1):
        date = f", {result['published_date']}" if result.get("published_date") else ""
        snippet = " ".join((result.get("content") or "").split())[:SNIPPET_CHARS]
        lines.append(f"{number}. {result.get('title', '')} ({result.get('url', '')}{date})\n   {snippet}")
    return "\n".join(lines) or "No results."


class WebSearch:
    def __init__(self, key=None, post=post):
        self.key = key or os.environ.get("TAVILY_API_KEY")
        self._post = post

    @property
    def available(self):
        return bool(self.key)

    async def search(self, query, topic="general", time_range=None):
        query = query.strip()
        if not query:
            raise ToolError("Say what to search for.")
        body = {"query": query, "search_depth": "basic", "max_results": MAX_RESULTS, "include_answer": True,
                "topic": topic if topic in ("general", "news") else "general"}
        if time_range in ("day", "week", "month", "year"):
            body["time_range"] = time_range
        try:
            data = await asyncio.to_thread(self._post, URL, body, self.key)
        except OSError as e:  # includes HTTP errors and timeouts
            raise ToolError(f"The web search failed: {e}")
        return format_results(data)

    def tools(self):
        if not self.available:
            return []

        async def run(args):
            return await self.search(args.get("query", ""), args.get("topic", "general"), args.get("time_range"))

        return [Tool(
            name="web_search",
            description=("Search the web for current information: news, weather, opening hours, prices, facts that "
                         "may have changed. The query goes to an outside search service, so leave private details "
                         "out of it."),
            input_schema={"type": "object", "properties": {
                "query": {"type": "string"},
                "topic": {"type": "string", "enum": ["general", "news"]},
                "time_range": {"type": "string", "enum": ["day", "week", "month", "year"],
                               "description": "Only results from this recent period."}},
                "required": ["query"]},
            run=run, source="natro")]
