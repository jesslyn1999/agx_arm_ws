#!/usr/bin/env python3
"""Minimal Qwen3-Omni-Flash voice MVP via DashScope OpenAI compatible mode.

This is a turn-based voice loop:
1. record a short microphone clip with arecord
2. send the WAV as input_audio to qwen3-omni-flash
3. stream text to stdout and audio to aplay

The compatible Chat Completions API is not a full-duplex realtime audio API.
Use the realtime WebSocket model later if strict low-latency barge-in is needed.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
from contextlib import suppress

import httpx
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3-omni-flash"
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
CHANNELS = 1


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('\"').strip("'")
        os.environ.setdefault(key, value)


class RawAudioPlayer:
    """Streams raw 24 kHz PCM audio chunks returned by Qwen Omni to aplay."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [
                "aplay",
                "-q",
                "-f",
                "S16_LE",
                "-r",
                str(OUTPUT_SAMPLE_RATE),
                "-c",
                str(CHANNELS),
                "-t",
                "raw",
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def write(self, data: bytes) -> None:
        if self.process.stdin is None:
            return
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def close(self) -> None:
        with suppress(BrokenPipeError):
            if self.process.stdin:
                self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
            with suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=1.0)
        if self.process.poll() is None:
            self.process.kill()


def record_wav(duration_sec: int) -> Path:
    fd, name = tempfile.mkstemp(prefix="qwen_omni_input_", suffix=".wav")
    os.close(fd)
    path = Path(name)
    subprocess.run(
        [
            "arecord",
            "-q",
            "-f",
            "S16_LE",
            "-r",
            str(INPUT_SAMPLE_RATE),
            "-c",
            str(CHANNELS),
            "-t",
            "wav",
            "-d",
            str(int(duration_sec)),
            str(path),
        ],
        check=True,
    )
    return path


def encode_wav_data_uri(path: Path) -> str:
    return "data:audio/wav;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def build_user_content(audio_data_uri: str, user_prompt: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "input_audio",
            "input_audio": {
                "data": audio_data_uri,
                "format": "wav",
            },
        },
        {
            "type": "text",
            "text": user_prompt,
        },
    ]


def run_one_turn(client: OpenAI, args: argparse.Namespace, messages: list[dict[str, Any]]) -> str:
    print(f"Recording {args.duration_sec:g}s...", flush=True)
    wav_path = record_wav(args.duration_sec)

    try:
        messages.append(
            {
                "role": "user",
                "content": build_user_content(encode_wav_data_uri(wav_path), args.user_prompt),
            }
        )
    finally:
        with suppress(FileNotFoundError):
            wav_path.unlink()

    print("Assistant: ", end="", flush=True)
    player = RawAudioPlayer() if args.play_audio else None
    assistant_text = ""

    try:
        completion = client.chat.completions.create(
            model=args.model,
            messages=messages,
            modalities=["text", "audio"] if args.play_audio else ["text"],
            audio={"voice": args.voice, "format": "wav"} if args.play_audio else None,
            extra_body={"enable_thinking": False},
            stream=True,
            stream_options={"include_usage": True},
        )

        for chunk in completion:
            if not chunk.choices:
                if args.verbose:
                    print(f"\n[usage] {chunk.usage}", file=sys.stderr)
                continue

            delta = chunk.choices[0].delta
            text_delta = getattr(delta, "content", None)
            if text_delta:
                assistant_text += text_delta
                print(text_delta, end="", flush=True)

            audio_delta = getattr(delta, "audio", None)
            if audio_delta and player:
                audio_data = audio_delta.get("data") if isinstance(audio_delta, dict) else None
                if audio_data:
                    player.write(base64.b64decode(audio_data))
    finally:
        if player:
            player.close()

    print()
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text})
    return assistant_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qwen3-Omni-Flash compatible-mode voice MVP")
    parser.add_argument(
        "--base-url",
        default=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
        help="DashScope Beijing compatible-mode base URL.",
    )
    parser.add_argument("--model", default=os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL), help="Model name.")
    parser.add_argument("--voice", default="Ethan", help="Output voice, for example Ethan, Tina, Cherry.")
    parser.add_argument("--duration-sec", type=int, default=4, help="Recording duration for each turn in whole seconds.")
    parser.add_argument("--no-play-audio", action="store_false", dest="play_audio", help="Request text only and skip TTS playback.")
    parser.add_argument(
        "--user-prompt",
        default=(
            "请听这段语音并直接回答。你是面向老年人的机器人语音助手，"
            "回答要简短、清楚、礼貌。不要控制机械臂。"
        ),
        help="Text instruction sent together with every audio clip.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print usage information.")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("Error: DASHSCOPE_API_KEY is not set.", file=sys.stderr)
        return 1
    if args.duration_sec <= 0:
        print("Error: --duration-sec must be a positive integer.", file=sys.stderr)
        return 1

    http_client = httpx.Client(proxy=None, trust_env=False)
    client = OpenAI(api_key=api_key, base_url=args.base_url, http_client=http_client)
    messages: list[dict[str, Any]] = []

    print("Qwen3-Omni-Flash voice MVP started. Press Enter to record, Ctrl+C to stop.")
    try:
        while True:
            input("\nPress Enter and speak after recording starts...")
            run_one_turn(client, args, messages)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Audio command failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        http_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
