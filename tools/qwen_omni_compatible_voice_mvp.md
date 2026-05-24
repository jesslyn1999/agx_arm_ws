# Qwen3-Omni-Flash Compatible-Mode Voice MVP

This MVP uses Alibaba Cloud Model Studio Beijing compatible mode:

- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Model: `qwen3-omni-flash`
- API: `chat.completions.create(..., stream=True)`

It listens to the microphone, starts recording when local voice activity is
detected, sends the captured speech as `input_audio`, then streams text and
audio output. This is not true full-duplex realtime audio; use the realtime
WebSocket API later for strict low-latency interruption.

## Setup

Create and use the local virtual environment under `tools/.venv`:

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
python3 -m venv tools/.venv
tools/.venv/bin/python -m pip install -r tools/requirements.txt
```

The script reads `.env` from the workspace root automatically. Required keys:

```dotenv
DASHSCOPE_API_KEY=your_api_key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3-omni-flash
```

## Run

```bash
cd /home/yuuki/Documents/ROBOT/agx_arm_ws
tools/.venv/bin/python tools/qwen_omni_compatible_voice_mvp.py
```

Options:

```bash
tools/.venv/bin/python tools/qwen_omni_compatible_voice_mvp.py --voice Ethan
tools/.venv/bin/python tools/qwen_omni_compatible_voice_mvp.py --vad-threshold 700 --vad-silence-ms 1200
tools/.venv/bin/python tools/qwen_omni_compatible_voice_mvp.py --manual --duration-sec 5
tools/.venv/bin/python tools/qwen_omni_compatible_voice_mvp.py --no-play-audio
```

## Dependencies

```bash
which arecord aplay
tools/.venv/bin/python -c 'import openai; print(openai.__version__)'
```

Install dependencies if missing:

```bash
tools/.venv/bin/python -m pip install -r tools/requirements.txt
```

## Startup Welcome Audio

Generate the welcome WAV once:

```bash
tools/.venv/bin/python tools/generate_welcome_audio.py
```

The main program plays `tools/welcome_hidilao.wav` before it starts listening.
Skip it when debugging:

```bash
tools/.venv/bin/python tools/qwen_omni_compatible_voice_mvp.py --no-welcome
```

## Safety Boundary

This script only validates the voice interaction loop. It does not send robot
motion commands. Keep any future robot control behind intent parsing, joint-limit
checks, current-state reads, and explicit confirmation for risky actions.
