#!/usr/bin/env python3
"""Minimal realtime voice MVP for DashScope Qwen Omni.

This script uses the Qwen Omni realtime WebSocket API directly:
- microphone input: 16 kHz, 16-bit, mono PCM from arecord
- model audio output: 24 kHz, 16-bit, mono PCM to aplay

It intentionally does not control the robot arm. Keep robot actions behind a
separate intent and safety layer after the voice loop is validated.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
import uuid
from contextlib import suppress
from typing import Any

import websockets


DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3-omni-flash-realtime"

INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1


class AudioProcess:
    """Thin wrapper around arecord/aplay so the MVP avoids PyAudio setup."""

    def __init__(self, command: list[str], *, stdin: bool = False, stdout: bool = False) -> None:
        self.command = command
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin else None,
            stdout=subprocess.PIPE if stdout else None,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def terminate(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        with suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=1.0)
        if self.process.poll() is None:
            self.process.kill()


def build_session_update(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "event_id": f"event_{uuid.uuid4().hex}",
        "type": "session.update",
        "session": {
            "modalities": ["text", "audio"],
            "instructions": args.instructions,
            "voice": args.voice,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "smooth_output": args.smooth_output,
            "turn_detection": {
                "type": "server_vad",
                "threshold": args.vad_threshold,
                "silence_duration_ms": args.vad_silence_ms,
            },
        },
    }


async def send_microphone_audio(ws: websockets.ClientConnection, args: argparse.Namespace) -> None:
    bytes_per_chunk = int(INPUT_SAMPLE_RATE * SAMPLE_WIDTH_BYTES * CHANNELS * args.chunk_ms / 1000)
    recorder = AudioProcess(
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
            "raw",
        ],
        stdout=True,
    )
    assert recorder.process.stdout is not None

    try:
        while True:
            chunk = await asyncio.to_thread(recorder.process.stdout.read, bytes_per_chunk)
            if not chunk:
                raise RuntimeError("arecord stopped; check microphone device and permissions.")
            await ws.send(
                json.dumps(
                    {
                        "event_id": f"event_{uuid.uuid4().hex}",
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        recorder.terminate()


async def receive_and_play(ws: websockets.ClientConnection, args: argparse.Namespace) -> None:
    player = AudioProcess(
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
        stdin=True,
    )
    assert player.process.stdin is not None

    current_text = ""

    try:
        async for raw_message in ws:
            event = json.loads(raw_message)
            event_type = event.get("type", "")

            # ── Text output ────────────────────────────────────────────────
            if event_type in {
                "response.text.delta",
                "response.output_text.delta",
                "response.audio_transcript.delta",
            }:
                text_delta = event.get("delta", "")
                current_text += text_delta
                print(text_delta, end="", flush=True)
            elif event_type in {
                "response.text.done",
                "response.output_text.done",
                "response.audio_transcript.done",
            }:
                if current_text:
                    print()
                    current_text = ""

            # ── Audio output ───────────────────────────────────────────────
            elif event_type == "response.audio.delta":
                audio_delta = event.get("delta")
                if audio_delta:
                    player.process.stdin.write(base64.b64decode(audio_delta))
                    player.process.stdin.flush()

            # ── Interruption and diagnostics ───────────────────────────────
            elif event_type == "input_audio_buffer.speech_started":
                # User starts speaking while the model is talking. Drop queued
                # audio locally; the server VAD also cancels the active response.
                with suppress(BrokenPipeError):
                    player.process.stdin.close()
                player.terminate()
                player = AudioProcess(
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
                    stdin=True,
                )
                assert player.process.stdin is not None
                if args.verbose:
                    print("\n[interrupted by user speech]")
            elif event_type == "error":
                print(f"\n[server error] {json.dumps(event, ensure_ascii=False)}", file=sys.stderr)
            elif args.verbose and event_type:
                print(f"\n[event] {event_type}", file=sys.stderr)
    finally:
        with suppress(BrokenPipeError):
            if player.process.stdin:
                player.process.stdin.close()
        player.terminate()


async def run(args: argparse.Namespace) -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set.")

    url = f"{args.url}?model={args.model}"
    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    async with websockets.connect(
        url,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=20,
        max_size=None,
    ) as ws:
        await ws.send(json.dumps(build_session_update(args), ensure_ascii=False))
        print("Realtime voice session started. Speak into the microphone. Press Ctrl+C to stop.")
        sender = asyncio.create_task(send_microphone_audio(ws, args))
        receiver = asyncio.create_task(receive_and_play(ws, args))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_EXCEPTION)

        for task in pending:
            task.cancel()
        for task in done:
            task.result()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qwen Omni realtime voice MVP")
    parser.add_argument("--url", default=DEFAULT_URL, help="DashScope realtime WebSocket URL.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Realtime model name.")
    parser.add_argument("--voice", default="Ethan", help="Output voice name, for example Ethan.")
    parser.add_argument("--chunk-ms", type=int, default=40, help="Microphone chunk size in ms.")
    parser.add_argument("--vad-threshold", type=float, default=0.5, help="Server VAD speech threshold.")
    parser.add_argument("--vad-silence-ms", type=int, default=900, help="Silence duration before turn ends.")
    parser.add_argument(
        "--smooth-output",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use Qwen3-Omni-Flash-Realtime colloquial response style.",
    )
    parser.add_argument(
        "--instructions",
        default=(
            "你是一个面向老年人的机器人语音助手。回答要简短、慢一点、礼貌，"
            "如果用户表达停止、取消、别动或等一下，先确认已停止。"
        ),
        help="System instructions for the realtime session.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print realtime event names.")
    return parser.parse_args()


def main() -> int:
    signal.signal(signal.SIGINT, signal.default_int_handler)
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
