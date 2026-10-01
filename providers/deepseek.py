import os

from openai import OpenAI

import config
from orchestrator.events import emit, streaming


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"


def run_deepseek(prompt: str) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY no está configurada."
        )

    client = OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
    )

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a software engineering agent. "
                    "Analyze programming tasks carefully and "
                    "follow the user's instructions precisely."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        stream=streaming.get(),
    )

    if streaming.get():
        emit("stream_start", "DeepSeek")
        chunks = []
        try:
            for item in response:
                chunk = item.choices[0].delta.content if item.choices else None
                if chunk:
                    chunks.append(chunk)
                    emit("delta", chunk)
            return "".join(chunks)
        finally:
            response.close()
    return response.choices[0].message.content
