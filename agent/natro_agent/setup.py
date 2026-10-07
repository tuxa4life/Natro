"""Builds Natro the same way for the service and the typed chat: her own tools, MCP servers, Gemini."""
import os

from natro_agent.agent import Agent
from natro_agent.config import MCP_CONFIG, ROOT
from natro_agent.gemini import Gemini
from natro_agent.google_tools import TOKEN_FILE, Google, GoogleAPI
from natro_agent.memory import Memory
from natro_agent.notes import Notes
from natro_agent.spotify import TOKEN_FILE as SPOTIFY_TOKEN_FILE, Spotify, SpotifyAPI
from natro_agent.websearch import WebSearch


async def build_agent(servers, model=None):
    """servers: an MCPServers, started here (close it when done)."""
    memory = Memory(ROOT / "memory")
    tools = memory.tools() + Notes(ROOT / "notes").tools() + WebSearch().tools()
    # Calendar, Tasks and Gmail once the owner has signed in (natro_agent.google_signin).
    if TOKEN_FILE.exists():
        tools += Google(GoogleAPI.from_token()).tools()
    # Spotify once the owner has signed in (natro_agent.spotify_signin).
    if SPOTIFY_TOKEN_FILE.exists():
        tools += Spotify(SpotifyAPI.from_token()).tools()
    tools += await servers.start(MCP_CONFIG)
    # The free Gemini tier only when a key is set; otherwise everything goes to Claude.
    gemini = Gemini() if os.environ.get("GEMINI_API_KEY") else None
    options = {"model": model} if model else {}
    return Agent(tools, memory=memory, gemini=gemini, **options)
