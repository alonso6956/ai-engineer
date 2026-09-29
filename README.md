# AI Engineer

El repositorio objetivo se indica explícitamente. Puede estar en cualquier
directorio, independientemente de dónde esté instalado `ai-engineer`:

```bash
python main.py --project "~/Projects/my-project" --goal "Implement inventory system"
```

Este comando crea un plan con `CodexArchitect`, lo persiste con `TaskManager`
y lo ejecuta con `PlanRunner`. El proyecto debe ser un repositorio Git con un
commit inicial y sin cambios pendientes. La ejecución utiliza los proveedores,
validaciones y checkpoints existentes.

Los modos de proveedor siguen disponibles:

```bash
python main.py qwen "Explain this algorithm"
python main.py deepseek "Review this approach"
python main.py codex "Implement this feature" --project ../my-project
```

Codex requiere ahora `--project`; ya no toma el CWD como proyecto implícito.

Para retomar un plan después de `Ctrl+C`:

```bash
python main.py --project "~/Projects/my-project" --resume
```

`PlanRunner` recupera la tarea interrumpida con `Scheduler.resume_task()` y
después continúa las tareas pendientes. Valida que el checkpoint corresponda
al proyecto, la tarea y sus permisos de tests, además de comprobar Git HEAD.
Conserva los contadores de fallos y el presupuesto consumido. La tarea se
reinicia desde el checkpoint Git; no se recupera la conversación del worker.
En la CLI, `Ctrl+C` termina con código 130 sin imprimir un traceback; las APIs
Python siguen propagando `KeyboardInterrupt` para que el llamador lo gestione.

## Resolución de rutas

- `paths.py` obtiene `BASE_DIR` desde `__file__` y define
  `WORKSPACE_DIR = BASE_DIR / "workspace"`. `config.py` expone ambos valores.
- Las entradas se normalizan con `Path(path).expanduser().resolve()`.
  Las rutas relativas se interpretan respecto al CWD al recibirlas;
  después quedan ancladas como rutas absolutas.
- `workspace/tasks.json` y `workspace/state.json` pertenecen a `ai-engineer`,
  incluso al ejecutar `main.py` desde otro directorio. Las rutas personalizadas
  de `TaskManager` y `StateManager` también se normalizan al construirlos.
- Las rutas absolutas persistidas identifican el proyecto para recuperar una
  ejecución. No se reinterpretan buscando un proyecto similar en el home.
  Un checkpoint de otra máquina conserva su ubicación original; trasladar el
  código no migra automáticamente el proyecto de ese checkpoint.
- La CLI rechaza un nuevo objetivo si hay un plan sin terminar o una tarea
  interrumpida. Así conserva los datos para las APIs de recuperación existentes,
  incluida `Scheduler(project_root).resume_task()`, que valida proyecto y HEAD.
- Git, pytest y Codex reciben `cwd` explícito. Pytest usa el mismo intérprete
  que ejecuta `ai-engineer` (`sys.executable`).
- Las validaciones ejecutan Python con `-B` y `PYTHONDONTWRITEBYTECODE=1`
  para no crear ni modificar archivos `.pyc`, incluso si el proyecto los tiene
  versionados. Codex recibe esa variable y la instrucción de usarla al ejecutar
  pytest. La política sigue revisando todos los cambios y exigiendo autorización
  para modificar tests fuente.

Los entornos `env` y `.venv` contienen rutas absolutas generadas por Python;
deben recrearse al trasladar el proyecto. La cadena `QWEN_MODEL` es el
identificador enviado al servidor remoto, no una ruta local que deba resolverse.

## Validación

En un entorno con `python-dotenv`, `requests`, `openai` y `pytest` instalados:

```bash
python -m compileall -q config.py paths.py main.py orchestrator providers tools tests
python -m pytest -q
```

Las pruebas usan repositorios temporales, simulan las llamadas a modelos y
cubren rutas absolutas, relativas y con `~`, ejecución desde otro CWD,
persistencia y recuperación, validación de HEAD y protección contra traversal
y enlaces simbólicos que apunten fuera del proyecto.

### Comprobar DeepSeek sin generar tokens

Con `DEEPSEEK_API_KEY` configurada en `.env`:

```bash
DEEPSEEK_LIVE_TEST=1 python -m pytest -q tests/test_deepseek.py
```

La prueba real consulta únicamente `GET /models`: verifica conectividad,
autenticación y disponibilidad del modelo configurado, sin enviar prompts ni
solicitar generación. Las otras dos pruebas simulan el SDK para comprobar la
configuración, la lectura de la respuesta y el error por clave ausente.
La suite normal omite la conexión real salvo que se active esa variable.
Esta comprobación no valida saldo ni la generación de respuestas.
