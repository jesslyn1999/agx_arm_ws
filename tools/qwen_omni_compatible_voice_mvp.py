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
import wave
from array import array
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
ENV_FILE = ROOT_DIR / ".env"
DEFAULT_PROMPT_FILE = TOOLS_DIR / "Prompt.md"

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3-omni-flash"
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2


def load_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    prompt = path.read_text().strip()
    if not prompt:
        raise ValueError(f"Prompt file is empty: {path}")
    return prompt


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


class PcmAudioSink:
    """Streams Qwen Omni 24 kHz, 16-bit, mono PCM output to aplay."""

    def __init__(self, *, keep_file: bool = False) -> None:
        self.keep_file = keep_file
        self.bytes_written = 0
        self._debug_path: Path | None = None
        self._debug_file = None
        if keep_file:
            fd, name = tempfile.mkstemp(prefix="qwen_omni_output_", suffix=".pcm")
            os.close(fd)
            self._debug_path = Path(name)
            self._debug_file = self._debug_path.open("wb")

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
        if self._debug_file:
            self._debug_file.write(data)
        if self.process.stdin:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        self.bytes_written += len(data)

    def close(self) -> None:
        if self._debug_file:
            self._debug_file.close()
            if self._debug_path:
                print(f"[debug audio pcm] {self._debug_path}", file=sys.stderr)
        with suppress(BrokenPipeError):
            if self.process.stdin:
                self.process.stdin.close()
        with suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=10.0)
        if self.process.poll() is None:
            self.process.terminate()
            with suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=1.0)
        if self.process.poll() is None:
            self.process.kill()


def get_audio_delta_data(audio_delta: Any) -> str | None:
    if audio_delta is None:
        return None
    if isinstance(audio_delta, dict):
        return audio_delta.get("data")
    return getattr(audio_delta, "data", None)


def write_wav_from_pcm(pcm_data: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix="qwen_omni_input_", suffix=".wav")
    os.close(fd)
    path = Path(name)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(INPUT_SAMPLE_RATE)
        wav_file.writeframes(pcm_data)
    return path


def pcm_rms(pcm_data: bytes) -> float:
    if not pcm_data:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm_data[: len(pcm_data) - (len(pcm_data) % SAMPLE_WIDTH_BYTES)])
    if not samples:
        return 0.0
    return (sum(int(sample) * int(sample) for sample in samples) / len(samples)) ** 0.5


def record_wav_fixed(duration_sec: int) -> Path:
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


def record_wav_auto(args: argparse.Namespace) -> Path:
    frame_bytes = int(INPUT_SAMPLE_RATE * SAMPLE_WIDTH_BYTES * CHANNELS * args.vad_frame_ms / 1000)
    silence_frames = max(1, int(args.vad_silence_ms / args.vad_frame_ms))
    min_frames = max(1, int(args.min_record_sec * 1000 / args.vad_frame_ms))
    max_frames = max(min_frames, int(args.max_record_sec * 1000 / args.vad_frame_ms))
    preroll_frames = max(1, int(args.vad_prefix_ms / args.vad_frame_ms))

    recorder = subprocess.Popen(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    assert recorder.stdout is not None

    print("Listening...", flush=True)
    started = False
    silent_count = 0
    speech_frame_count = 0
    preroll: deque[bytes] = deque(maxlen=preroll_frames)
    speech_chunks: list[bytes] = []

    try:
        while True:
            chunk = recorder.stdout.read(frame_bytes)
            if not chunk:
                raise RuntimeError("arecord stopped; check microphone device and permissions.")

            level = pcm_rms(chunk)
            if args.verbose:
                print(f"\rlevel={level:7.1f}", end="", file=sys.stderr, flush=True)

            if not started:
                preroll.append(chunk)
                if level >= args.vad_threshold:
                    started = True
                    speech_chunks.extend(preroll)
                    speech_frame_count = len(speech_chunks)
                    print("Speech detected; recording...", flush=True)
                continue

            speech_chunks.append(chunk)
            speech_frame_count += 1
            silent_count = silent_count + 1 if level < args.vad_threshold else 0

            if speech_frame_count >= min_frames and silent_count >= silence_frames:
                break
            if speech_frame_count >= max_frames:
                print("Max recording length reached; sending...", flush=True)
                break
    finally:
        if recorder.poll() is None:
            recorder.terminate()
            with suppress(subprocess.TimeoutExpired):
                recorder.wait(timeout=1.0)
        if recorder.poll() is None:
            recorder.kill()

    if args.verbose:
        print(file=sys.stderr)
    return write_wav_from_pcm(b"".join(speech_chunks))


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


def run_one_turn(client: OpenAI, args: argparse.Namespace, messages: list[dict[str, Any]], wav_path: Path) -> str:
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
    audio_sink = PcmAudioSink(keep_file=args.keep_output_audio) if args.play_audio else None
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
            audio_data = get_audio_delta_data(audio_delta)
            if audio_data and audio_sink:
                audio_sink.write(base64.b64decode(audio_data))
    finally:
        if audio_sink:
            if args.verbose:
                print(f"\n[audio bytes] {audio_sink.bytes_written}", file=sys.stderr)
            audio_sink.close()

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
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT_FILE, help="System prompt markdown file.")
    parser.add_argument("--manual", action="store_true", help="Use Enter-to-record mode instead of automatic voice detection.")
    parser.add_argument("--duration-sec", type=int, default=4, help="Manual-mode recording duration in whole seconds.")
    parser.add_argument("--min-record-sec", type=float, default=0.7, help="Minimum automatic recording length after speech starts.")
    parser.add_argument("--max-record-sec", type=float, default=12.0, help="Maximum automatic recording length after speech starts.")
    parser.add_argument("--vad-threshold", type=float, default=500.0, help="RMS threshold for local speech detection.")
    parser.add_argument("--vad-silence-ms", type=int, default=900, help="Silence duration before automatic turn end.")
    parser.add_argument("--vad-prefix-ms", type=int, default=300, help="Audio kept before speech detection.")
    parser.add_argument("--vad-frame-ms", type=int, default=30, help="Local VAD frame size in milliseconds.")
    parser.add_argument("--no-play-audio", action="store_false", dest="play_audio", help="Request text only and skip TTS playback.")
    parser.add_argument("--keep-output-audio", action="store_true", help="Keep the last assistant PCM file in /tmp for debugging.")
    parser.add_argument(
        "--user-prompt",
        default="请听这段语音，并按照系统提示词中的服务流程继续对话。",
        help="Short text instruction sent together with every audio clip.",
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
    if args.min_record_sec <= 0 or args.max_record_sec <= 0 or args.max_record_sec < args.min_record_sec:
        print("Error: automatic recording durations are invalid.", file=sys.stderr)
        return 1
    if args.vad_threshold <= 0 or args.vad_silence_ms <= 0 or args.vad_frame_ms <= 0:
        print("Error: VAD settings must be positive.", file=sys.stderr)
        return 1

    system_prompt = load_prompt(args.prompt_file)
    http_client = httpx.Client(proxy=None, trust_env=False)
    client = OpenAI(api_key=api_key, base_url=args.base_url, http_client=http_client)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    mode = "manual" if args.manual else "automatic"
    print(f"Qwen3-Omni-Flash voice MVP started in {mode} mode. Press Ctrl+C to stop.")
    try:
        while True:
            if args.manual:
                input("\nPress Enter and speak after recording starts...")
                print(f"Recording {args.duration_sec:g}s...", flush=True)
                wav_path = record_wav_fixed(args.duration_sec)
            else:
                wav_path = record_wav_auto(args)
            run_one_turn(client, args, messages, wav_path)
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
