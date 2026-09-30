# AI Engineer

## RAPHAEL — interfaz interactiva

Sin argumentos, la entrada abre una sesión de terminal persistente:

```bash
python3 main.py
```

```text
raphael › /project ~/Projects/my-project
raphael › Add inventory persistence to the player system.
raphael › /status
raphael › /exit
```

RAPHAEL es una interfaz para el agente de programación. El texto normal ejecuta
un objetivo sobre el proyecto seleccionado, incluyendo las revisiones, pruebas
y commits que realiza el motor existente.

| Comando | Acción |
| --- | --- |
| `/project <path>` | Selecciona un repositorio Git; acepta `~`, rutas relativas y espacios, con o sin comillas. |
| `/project` | Muestra el proyecto activo. |
| `/new <name> [path]` | Crea un repositorio Git con commit inicial y lo selecciona. El directorio padre predeterminado es el CWD. |
| `/status` | Muestra el proyecto activo, el plan guardado y el checkpoint. |
| `/plan` | Lista las tareas y sus estados. |
| `/budget` | Muestra el consumo acumulado del plan y los límites de `BudgetLimits`. |
| `/resume` | Usa la recuperación existente con `retry_failed=True`. |
| `/clear` | Limpia la pantalla y redibuja la cabecera conservando la selección y los archivos. |
| `/help` | Muestra la ayuda. |
| `/exit`, `/quit` | Termina la sesión. |

`Ctrl+C` cancela la operación actual y vuelve al prompt; en reposo mantiene
abierta la sesión. `Ctrl+D` termina la sesión. Los errores se muestran en la
terminal sin cerrar la interfaz.

El proyecto activo se mantiene solamente durante la sesión. Los comandos de
consulta muestran el plan global de `workspace`, identificado por su ruta,
incluso si pertenece a otro proyecto. Seleccionar otro repositorio no borra ni
reemplaza ese plan. `/resume` exige que el proyecto seleccionado coincida.
Las operaciones son síncronas: no se aceptan nuevos comandos mientras el motor
trabaja. Esta versión no incluye una TUI de pantalla completa ni historial
persistente; las terminales estrechas o sin Unicode usan una presentación simple.

`cli/raphael.py` gestiona la sesión, `cli/display.py` presenta la información y
`cli/workflows.py` comparte las operaciones de objetivo y recuperación con la
CLI no interactiva. El motor conserva sus responsabilidades.

Para crear una base de proyecto sin invocar modelos:

```text
raphael › /new game-editor "~/My Projects"
```

El directorio padre debe existir. El nombre debe ser un único componente, sin
`/`, `\`, `.` ni `..` como nombre completo. Un destino existente, incluso un
enlace simbólico, se rechaza sin modificarlo. `/new` crea `README.md`, un
`.gitignore` mínimo para archivos locales de editores y del sistema, y el
commit `Initial commit`; después usa la misma selección que `/project`.

Git usa la identidad y configuración existentes. Si falta la identidad requerida
por Git, el error indica configurar `user.name` y `user.email`; RAPHAEL no
inventa una identidad ni cambia configuración global. Si falla algún paso,
la carpeta nueva se conserva para inspección y el proyecto activo anterior
permanece seleccionado. `/new` no reusa carpetas de intentos anteriores ni
cambia los planes guardados; un plan pendiente sigue sujeto a las reglas
habituales de recuperación.

## CLI no interactiva

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
python main.py local "Explain this algorithm"
python main.py qwen "Explain this algorithm"  # Alias legado del modelo local configurado
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
deben recrearse al trasladar el proyecto. `LOCAL_MODEL_NAME` es el identificador
enviado a llama.cpp, no una ruta local que deba resolverse.

## Modelo local configurable

El proveedor `providers/local.py` usa la API compatible con OpenAI del servidor
llama.cpp existente. Su modelo predeterminado es
`Ternary-Bonsai-2-27B-PQ2_0.gguf`. RAPHAEL no abre ni carga archivos GGUF.

Puedes configurar estas variables en `.env` o en el entorno antes de iniciar
RAPHAEL (las variables del entorno tienen prioridad sobre `.env`):

| Variable | Uso y valor predeterminado |
| --- | --- |
| `LOCAL_MODEL_BASE_URL` | Base de la API: `http://192.168.3.252:8080/v1`. |
| `LOCAL_MODEL_NAME` | ID del modelo solicitado; por defecto, el nombre del GGUF indicado arriba. |
| `LOCAL_MODEL_LABEL` | Nombre corto en la cabecera: `Local`. Puede ser `Bonsai`. |

El ID que publica llama.cpp puede ser un alias distinto del nombre del archivo.
Puedes comprobarlo manualmente, sin generar una respuesta:

```bash
env/bin/python - <<'PY'
import config
import requests

response = requests.get(config.LOCAL_MODEL_BASE_URL.rstrip('/') + '/models', timeout=15)
response.raise_for_status()
print('Configured model:', config.LOCAL_MODEL_NAME)
print('Server model IDs:', [model['id'] for model in response.json()['data']])
PY
```

Si el ID es distinto, establece `LOCAL_MODEL_NAME` al ID publicado. Para probar
una petición real al modelo local configurado:

```bash
env/bin/python main.py local "Responde únicamente OK."
```

Estas comprobaciones son manuales; las pruebas automatizadas simulan el HTTP.
El proveedor conserva el timeout de 3600 segundos, temperatura 0.2, manejo de
errores y protocolo de mensajes existentes. No se añade descubrimiento
automático ni formato de prompts específico de Bonsai.

La cadena es `LOCAL → DEEPSEEK → CODEX`: dos fallos locales llevan a DeepSeek,
y un fallo de DeepSeek lleva a Codex, salvo umbrales configurados por el llamador.
El candidato local conserva la revisión de DeepSeek. Las llamadas locales se
cuentan sin límite predeterminado; DeepSeek mantiene 3 llamadas y Codex 1.

### Compatibilidad con Qwen y checkpoints existentes

Se conservan deliberadamente las claves JSON `qwen_failures` y `qwen_calls`,
incluido `ProjectPlan.budget_usage`, y los argumentos antiguos de construcción
de los dataclasses de estado/presupuesto. Son contadores del **rol local**, no
de un modelo particular. En ejecución se exponen `TaskState.local_failures`,
`BudgetUsage.local_calls` y `BudgetLimits.local_calls`, sin copiar ni reiniciar
contadores. No hace falta reescribir `tasks.json` ni `state.json`.

`Provider.LOCAL` usa el valor `local`; `Provider.QWEN` es un alias compatible.
Los valores persistidos `qwen` siguen aceptándose, también en los intentos
guardados. La recuperación reconoce ambos valores como el mismo proveedor
para evitar registrar dos veces una interrupción.

`qwen` en la CLI, `providers.qwen.run_qwen`, `QWEN_URL` y `QWEN_MODEL` son
aliases de compatibilidad. La implementación HTTP sólo existe en `run_local`.
`Router` y `Scheduler` aceptan `local_max_failures`, manteniendo el argumento
antiguo `qwen_max_failures`; `Router.route` acepta `local_failures` y conserva
`qwen_failures` para llamadas existentes. Si se pasan ambos nombres, prevalece
el argumento `local_*`. Para cambiar de modelo en el futuro basta configurar
el ID y, opcionalmente, su etiqueta o la URL del servidor.

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
