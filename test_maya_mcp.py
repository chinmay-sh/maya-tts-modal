"""Gateway check with a fake synthesizer: no Modal account or GPU needed.

uv run python test_maya_mcp.py
"""
import asyncio
import base64

from fastmcp import Client
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from maya_mcp import MAX_CHARS, make_app

calls = []
WAV = b"RIFF fake wav"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream"}


async def fake(text, language, voice):
    calls.append((text, language, voice))
    return WAV


def post(http, body, token=None):
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    return http.post("/mcp", json=body, headers=HEADERS | auth)


http_app = make_app(fake, "secret")
# modal.run is the Host a deployed server sees, so this also proves FastMCP's host checks let it through
with TestClient(http_app, base_url="https://me--maya-tts-web.modal.run") as http:
    assert post(http, INIT).status_code == 401
    assert post(http, INIT, "wrong").status_code == 401
    r = post(http, INIT, "secret")
    assert r.status_code == 200, (r.status_code, r.text)
    assert "Maya2 Native TTS" in r.text


async def tool_calls():
    # The auth test above covers HTTP, so this talks to the tool over FastMCP's in-memory route
    mcp = http_app.state.fastmcp_server
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["synthesize_speech"]
        guide = tools[0].description
        assert "[whispers]" in guide and "Devanagari" in guide
        assert len(guide) < 2000, len(guide)  # clients may trim longer descriptions
        assert not calls, "listing tools must not touch the GPU"

        block = (await client.call_tool("synthesize_speech", {"input": "नमस्ते"})).content[0]
        assert block.type == "audio" and block.mime_type == "audio/wav", block
        assert base64.b64decode(block.data) == WAV
        assert calls == [("नमस्ते", "hi", "Vasant")]

        for bad in ({"input": ""}, {"input": "x" * (MAX_CHARS + 1)}, {"input": "hi", "voice": "Morgan"}, {"input": "hi", "language": "fr"}):
            try:
                await client.call_tool("synthesize_speech", bad)
            except ToolError:
                continue
            raise SystemExit(f"should be refused: {str(bad)[:60]}")
        assert len(calls) == 1, "a refused request must not reach the GPU"


asyncio.run(tool_calls())
print("gateway check ok")
