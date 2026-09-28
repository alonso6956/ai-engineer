import argparse
import os

import config
from providers.qwen import run_qwen
from providers.codex import run_codex
from providers.deepseek import run_deepseek

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "provider",
        choices=["qwen", "deepseek", "codex"],
    )

    parser.add_argument("prompt")

    args = parser.parse_args()

    if args.provider == "qwen":
        result = run_qwen(args.prompt)
    elif args.provider == "deepseek":
        result = run_deepseek(args.prompt)
    elif args.provider == "codex":
        result = run_codex(
            args.prompt,
            cwd=os.getcwd(),
        )

    print(result)


if __name__ == "__main__":
    main()