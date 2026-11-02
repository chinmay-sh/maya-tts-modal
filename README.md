# Maya2 Native MCP server on Modal

Serves [Maya2 Native](https://huggingface.co/maya-research/maya2-native) as a remote MCP server with one tool, `synthesize_speech(input, language, voice)`. It returns a 24 kHz mono 16-bit WAV.

Modal gives the server a public HTTPS URL, so no Cloudflare tunnel is needed. Every request must carry the bearer token from `.env`.

## Deploy

```bash
uv sync
```

```bash
uv run modal setup
```

```bash
uv run modal secret create maya-mcp --from-dotenv .env --force
```

```bash
uv run modal deploy maya_mcp.py
```

The first deploy builds the image on an L4, which downloads the weights and compiles the engine's kernels once. The deploy prints the URL. The MCP endpoint is that URL plus `/mcp`.

## Connect

Use the MCP endpoint with streamable HTTP transport and this header, taking the token from `.env`:

```
Authorization: Bearer <token>
```

In Claude Code:

```bash
claude mcp add --transport http maya-modal https://<workspace>--maya-tts-web.modal.run/mcp --header "Authorization: Bearer <token>"
```

## Cost

Modal's Starter plan includes $30 of compute a month. You pay only while the GPU runs. It stops after 3 idle minutes, and `max_containers=1` stops more than one running at a time.

The server asks for an L4 first and falls back to an A10 when no L4 is free. The log line `GPU:` shows which one a container got.

| GPU | GPU price | Hours for $30, with CPU and memory |
|---|---|---|
| L4 | $0.80 an hour | About 32 |
| A10 | $1.10 an hour | About 25 |

Modal needs a payment method on the workspace before it will run any GPU. Set a workspace budget in Modal's billing settings so usage cannot go past the $30.

See the spend with `uv run modal billing report --for today`.

## Measured

Measured on 2026-11-02; the cold start of 167 s was confirmed on an A10.

| | |
|---|---|
| Cold start, first call after idle | 79 to 167 s, plus any wait for a GPU |
| Warm call, 6.6 s line | About 4 s |
| Four parallel lines, 27 s of audio | About 4 s |
| Long clip, 263 s of audio | 127 s |

Clients built on the Python MCP SDK refuse any message over 1 MiB, which is about 16 s of audio. The Node SDK has no such limit and returned a 12.6 MB clip. Split longer text into several calls for Python clients.

## Speech tags

Tags go in the text, for example `[whispers] Stay quiet.` The model card calls them best effort. Tested on 2026-11-02 with two takes each:

| Input | Result |
|---|---|
| `[whispers]` | Works in Hindi and English on every take, at varying strength |
| `[shouting]`, `[excited]` | Pitch rises, more so in Hindi; not louder |
| `[crying]`, `[sad]` | Slower, with longer pauses |
| `[surprised]`, `[scared]` | No clear change |
| `[laughs]`, `[sighs]`, `[gasps]` | A pause, with no measurable sound; same as a made-up tag |
| `!` | Raises pitch in English, slightly in Hindi |
| `...` | No longer pause |

Any bracket, even a made-up one, tends to flatten the delivery and add pauses, so use only tags that have a clear effect. The numbers cannot judge how a clip sounds, so check by ear.

## Test

No Modal account or GPU needed:

```bash
uv run python test_maya_mcp.py
```
