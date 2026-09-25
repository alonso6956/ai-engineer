import os

from openai import OpenAI


DEEPSEEK_MODEL = "deepseek-flash"


def run_deepseek(prompt: str) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY no está configurada."
        )

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
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
        stream=False,
    )

    return response.choices[0].message.content