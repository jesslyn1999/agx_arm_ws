#!/usr/bin/env python3
"""Generate the startup welcome audio for the HiDilao voice MVP."""

from __future__ import annotations

import argparse
import base64
import os
import sys
import wave
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI


TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
ENV_FILE = ROOT_DIR / ".env"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3-omni-flash"
DEFAULT_OUTPUT = TOOLS_DIR / "welcome_hidilao.wav"
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))


def get_audio_delta_data(audio_delta: Any) -> str | None:
    if audio_delta is None:
        return None
    if isinstance(audio_delta, dict):
        return audio_delta.get("data")
    return getattr(audio_delta, "data", None)


def write_pcm_as_wav(path: Path, pcm_data: bytes) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate HiDilao startup welcome audio")
    parser.add_argument("--base-url", default=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--voice", default="Ethan")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--text",
        default="欢迎光临嗨递老火锅，我来陪您慢慢点单，咱们不着急。请您先说说，最近饮食上有没有什么需要注意的？",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("Error: DASHSCOPE_API_KEY is not set.", file=sys.stderr)
        return 1

    http_client = httpx.Client(proxy=None, trust_env=False)
    client = OpenAI(api_key=api_key, base_url=args.base_url, http_client=http_client)
    pcm_chunks: list[bytes] = []
    assistant_text = ""

    try:
        completion = client.chat.completions.create(
            model=args.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "请用亲切、清楚、偏慢的语速朗读下面这句欢迎语。"
                        "只输出这句欢迎语，不要扩写：" + args.text
                    ),
                }
            ],
            modalities=["text", "audio"],
            audio={"voice": args.voice, "format": "wav"},
            extra_body={"enable_thinking": False},
            stream=True,
            stream_options={"include_usage": True},
        )

        for chunk in completion:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text_delta = getattr(delta, "content", None)
            if text_delta:
                assistant_text += text_delta
            audio_data = get_audio_delta_data(getattr(delta, "audio", None))
            if audio_data:
                pcm_chunks.append(base64.b64decode(audio_data))
    finally:
        http_client.close()

    pcm_data = b"".join(pcm_chunks)
    if not pcm_data:
        print("Error: model returned no audio data.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_pcm_as_wav(args.output, pcm_data)
    print(f"Generated: {args.output}")
    print(f"Text: {assistant_text or args.text}")
    print(f"PCM bytes: {len(pcm_data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
