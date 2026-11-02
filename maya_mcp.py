"""Maya2 Native text-to-speech as a remote MCP server on Modal.

Two containers: a CPU gateway speaks MCP, and the L4 GPU runs only inside synthesize_speech.
Connecting a client or listing tools therefore never boots the GPU or spends much credit.
"""
import hmac
import io
import os
from typing import Annotated, Literal

import modal

MODEL = "maya-research/maya2-native"
MAYA_COMMIT = "c6b376868e268551d065c4206193e96257bef4a1"
SEATS = 4  # the engine batches this many parallel calls; memory grows with it, 8 seats peak at 10.9 GB
MAX_CHARS = 4000  # same limit as maya3.server

Language = Literal["as", "bn", "en", "gu", "hi", "kn", "ml", "mr", "or", "pa", "ta", "te"]
Voice = Literal[
    "Ahana", "Vaidehi", "Malini", "Charvi", "Jahnavi", "Anirban", "Nakul", "Vasant", "Siddhant", "Satvik",
    "Abhay", "Nivedita", "Ishwari", "Ritvik", "Vrinda", "Sreekar", "Kayal", "Shaunak", "Akshat", "Tanushree",
]

app = modal.App("maya-tts")


def bake():
    """Runs once at image build: stores the weights and compiled Triton kernels, so a cold start skips both."""
    from maya3 import Maya3TTS

    tts = Maya3TTS.from_pretrained(MODEL, max_seats=SEATS)
    tts.generate("नमस्ते, आप कैसे हैं?", lang="hi", voice="Vasant")
    tts.close()


gpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    # Separate steps: with the PyTorch index in play, uv takes its old packaging and maya3's build fails
    .uv_pip_install("torch==2.8.0", "torchaudio==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .uv_pip_install(f"maya3 @ git+https://github.com/MayaResearch/maya2-native@{MAYA_COMMIT}")
    .env({"HF_HOME": "/models", "TRITON_CACHE_DIR": "/models/triton"})
    .run_function(bake, gpu="L4")
    .env({"HF_HUB_OFFLINE": "1"})
)
web_image = modal.Image.debian_slim(python_version="3.12").uv_pip_install("fastmcp==4.1.5")


# A10 is the fallback because L4 capacity can run out; T4 is not an option, it has no native BF16
@app.cls(image=gpu_image, gpu=["L4", "A10"], max_containers=1, scaledown_window=180, timeout=600)
@modal.concurrent(max_inputs=SEATS)
class Maya:
    @modal.enter()
    def load(self):
        import torch
        from maya3 import Maya3TTS

        print("GPU:", torch.cuda.get_device_name())  # L4 and A10 bill differently
        self.tts = Maya3TTS.from_pretrained(MODEL, max_seats=SEATS)

    @modal.method()
    def synthesize(self, text: str, language: str, voice: str) -> bytes:
        import soundfile as sf

        wav = self.tts.generate(text, lang=language, voice=voice, normalize=True)
        out = io.BytesIO()
        sf.write(out, wav, 24000, format="WAV", subtype="PCM_16")
        return out.getvalue()

    @modal.exit()
    def close(self):
        self.tts.close()


class BearerAuth:
    """modal.run URLs are public, so every request must carry the token."""

    def __init__(self, app, token):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.responses import PlainTextResponse

            header = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(header, self.expected):
                return await PlainTextResponse("unauthorized", 401)(scope, receive, send)
        await self.app(scope, receive, send)


def make_app(synthesize, token):
    """The MCP gateway; synthesize is injected so the test runs without Modal or a GPU."""
    from fastmcp import FastMCP
    from fastmcp.utilities.types import Audio
    from pydantic import Field
    from starlette.middleware import Middleware

    mcp = FastMCP("Maya2 Native TTS")

    @mcp.tool
    async def synthesize_speech(
        input: Annotated[str, Field(min_length=1, max_length=MAX_CHARS)],
        language: Language = "hi",
        voice: Voice = "Vasant",
    ) -> Audio:
        """Speak text with Maya2 Native and return a 24 kHz mono WAV.

        Calls: use one call per narration line and send up to 4 in parallel, which is much faster.
        The first call after 3 idle minutes waits 1 to 3 minutes for the GPU.

        Writing: for Hindi and Hinglish, write everything in Devanagari, English words and names too
        (हब, राइट फ़्लैंक), because Latin letters read worse. Numbers and dates are expanded automatically.

        Tags go in the text and are best effort, tested by measurement:
        - [whispers] at the start of a line is the only reliable tag.
        - [shouting] and [excited] raise the pitch but are not louder.
        - [sad] and [crying] slow the line down and add pauses.
        - Avoid [laughs], [sighs], [gasps], [surprised] and [scared]. They add a pause or nothing,
          with no matching sound. Add laughs and sighs as sound effects in the edit instead.
        - Any other bracket can flatten the delivery, so never invent tags.

        Surprise: write it into the words with ! such as अरे! यह क्या हो गया!
        Pauses: "..." adds none. Split the line and add silence in the edit.
        Speed and pitch: there are no controls. Speech runs near 2.8 words a second, so adjust with
        ffmpeg afterwards.

        Voices: Vasant is the mature male narrator. Also Anirban (male narrator), Malini (mature female
        narrator), Siddhant (clear professional male), Vaidehi (calm female), Ahana (bright young female).
        """
        return Audio(data=await synthesize(input, language, voice), format="wav")

    return mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        middleware=[Middleware(BearerAuth, token=token)],
    )


@app.function(image=web_image, secrets=[modal.Secret.from_name("maya-mcp")], max_containers=1, timeout=660)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    return make_app(Maya().synthesize.remote.aio, os.environ["MCP_TOKEN"])
