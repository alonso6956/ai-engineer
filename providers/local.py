"""The configured local model, served by llama.cpp's chat completions API."""

import requests

import config


def run_local(prompt: str) -> str:
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
        },
        timeout=3600,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]
