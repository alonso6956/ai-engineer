from dotenv import load_dotenv

from paths import BASE_DIR, WORKSPACE_DIR

ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE)

QWEN_URL = "http://192.168.3.252:8080/v1"
QWEN_MODEL = r".\models\Qwen3.8-27B-UD-IQ4_XS.gguf"

CODEX_SANDBOX = "workspace-write"
