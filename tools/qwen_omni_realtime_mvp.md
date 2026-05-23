# Qwen Omni Realtime Voice MVP

This is a minimal microphone-to-speaker realtime voice loop for Alibaba Cloud
Model Studio Qwen Omni. It uses `qwen3-omni-flash-realtime` over the realtime
WebSocket API.

It does not send commands to the robot arm. Keep robot control behind a
separate intent parser and safety validator after the voice interaction is
stable.

## Requirements

- Linux audio tools: `arecord` and `aplay`
- Python package: `websockets`
- Environment variable: `DASHSCOPE_API_KEY`

Check dependencies:

```bash
which arecord aplay
python3 -c 'import websockets; print(websockets.__version__)'
```

Install missing Python dependency if needed:

```bash
python3 -m pip install websockets
```

## Run

```bash
export DASHSCOPE_API_KEY="your_api_key"
python3 tools/qwen_omni_realtime_mvp.py
```

Useful options:

```bash
python3 tools/qwen_omni_realtime_mvp.py --voice Ethan --vad-silence-ms 1200 --verbose
```

## Notes

- Input audio sent to the API is `pcm`, 16 kHz, mono, 16-bit.
- Output audio from the API is `pcm`, 24 kHz, mono, 16-bit.
- Server VAD is enabled. The default `--vad-silence-ms 900` is intentionally
  conservative for older users who may pause between phrases.
- The script resets local audio playback on `input_audio_buffer.speech_started`
  so user speech can interrupt model speech.
