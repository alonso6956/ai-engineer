import os

from dotenv import load_dotenv

from paths import BASE_DIR, WORKSPACE_DIR

ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE)

LOCAL_MODEL_BASE_URL = os.getenv("LOCAL_MODEL_BASE_URL", "http://192.168.3.252:8080/v1")
# This is the API model ID. Override it if llama.cpp serves the file under an alias.
LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "Ternary-Bonsai-2-27B-PQ2_0.gguf")
LOCAL_MODEL_LABEL = os.getenv("LOCAL_MODEL_LABEL", "Local")

# Legacy imports continue to refer to the configured local server/model.
QWEN_URL = LOCAL_MODEL_BASE_URL
QWEN_MODEL = LOCAL_MODEL_NAME

CODEX_SANDBOX = "workspace-write"
