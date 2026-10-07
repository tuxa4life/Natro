"""Type to Natro in English and read her reply: the agent without voice.

Natro's own tools (memory, notes, web search if TAVILY_API_KEY is set), MCP
servers from config/mcp.json, and the free Gemini tier for non-personal requests
(if GEMINI_API_KEY is set) are all on, as in the service. --dry-run-pc adds a pretend PC
whose tools (open, close, force-close an app) only record what they would do.
Confirmations are asked here. Each reply ends with the model, time and cost.

Usage:
    python -m natro_agent.chat
    python -m natro_agent.chat --dry-run-pc
    python -m natro_agent.chat --model claude-haiku-4-5
    python -m natro_agent.chat --once "what can you do?"
"""
import argparse
import asyncio
import sys

from natro_agent.config import load_env
from natro_agent.dryrun import PC_TOOLS, DryRunDevice
from natro_agent.mcp_client import MCPServers
from natro_agent.models import DEFAULT_MODEL, MODELS
from natro_agent.setup import build_agent
from natro_agent.tools import Device


class TypedChat(Device):
    name = "chat"
    label = "typed chat"

    async def confirm(self, question, details):
        answer = await asyncio.to_thread(input, f"\n  ? {question}\n    ({details}) [y/N] ")
        return answer.strip().lower() in ("y", "yes")


async def chat(args):
    servers = MCPServers()
    agent = await build_agent(servers, model=args.model)
    if args.dry_run_pc:
        agent.connect(DryRunDevice("pc", "PC", PC_TOOLS))
    typed = TypedChat()
    try:
        while True:
            text = args.once or (await asyncio.to_thread(input, "> ")).strip()
            if text.lower() in ("q", "quit", "exit"):
                break
            if not text:
                continue
            reply = await agent.respond(text, typed, on_text=lambda piece: print(piece, end="", flush=True))
            tools = f", tools: {', '.join(call['tool'] for call in reply.tool_calls)}" if reply.tool_calls else ""
            print(f"\n  ({reply.model}, {reply.seconds:.1f} s, ${reply.cost:.4f}{tools})\n")
            if args.once:
                break
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        await servers.close()
    print(f"This month so far: ${agent.ledger.spent:.2f} of ${agent.ledger.budget:g}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    parser.add_argument("--dry-run-pc", action="store_true", help="add a pretend PC with app tools")
    parser.add_argument("--once", help="send this one request and exit")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    asyncio.run(chat(args))


if __name__ == "__main__":
    main()
