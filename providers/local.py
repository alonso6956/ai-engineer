"""The configured local model, served by llama.cpp's chat completions API."""

import json
import requests

from orchestrator.events import emit, streaming

import config


def run_local(prompt: str) -> str:
    use_stream = streaming.get()
    response = requests.post(
        f"{config.LOCAL_MODEL_BASE_URL.rstrip('/')}/chat/completions",
        json={
            "model": config.LOCAL_MODEL_NAME,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a software engineering agent. "
                        "Follow the user's instructions precisely."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            **({"stream": True} if use_stream else {}),
        },
        timeout=3600,
        **({"stream": True} if use_stream else {}),
    )
    response.raise_for_status()
    if use_stream:
        response.encoding = "utf-8"
        emit("stream_start", "Local")
        chunks = []
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                data = json.loads(raw)
                choices = data.get("choices", [])
                chunk = choices[0].get("delta", {}).get("content", "") if choices else ""
                if chunk:
                    chunks.append(chunk)
                    emit("delta", chunk)
            return "".join(chunks)
        finally:
            response.close()
    data = response.json()
    return data["choices"][0]["message"]["content"]
