import requests

from config import QWEN_URL, QWEN_MODEL


def run_qwen(prompt: str) -> str:
    response = requests.post(
        f"{QWEN_URL}/chat/completions",
        json={
            "model": QWEN_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a software engineering agent. "
                        "Follow the user's instructions precisely."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.2,
        },
        timeout=3600,
    )

    response.raise_for_status()

    data = response.json()

    return data["choices"][0]["message"]["content"]