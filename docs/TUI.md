# Raphael TUI: implementación y entrega

## Arquitectura inicial

`main.py` abre `RaphaelCLI` sin argumentos y mantiene entradas directas de
providers, `--goal` y `--resume`. `cli/raphael.py` combina input y presentación,
pero `cli/workflows.py` ya comparte el backend. `CodexArchitect` inspecciona en
modo read-only y produce tareas; `PlanRunner` persiste/ejecuta el plan;
`Scheduler` controla checkpoints, presupuesto, rollback, política de cambios,
revisión, tests y commits. Workers: Local → DeepSeek → Codex; revisión DeepSeek
para candidatos locales y Codex según el router existente.

`LocalWorker` mantiene un contexto textual por tarea y ejecuta acciones JSON
con `FileSystem`, `GitManager` y `TestRunner`. No había conversación global
reutilizable ni streaming: HTTP local sin streaming, DeepSeek `stream=False`,
Codex mediante `subprocess.run`. Las rutas ya usaban `expanduser().resolve()`;
los tools restringen acceso al project root. `/new` ya creaba repositorios con
README, .gitignore y commit inicial. El plan y checkpoint son globales, en
`workspace`, y la recuperación valida proyecto y HEAD.

Estado inicial: working tree limpio; **138 passed, 1 skipped**.

## Refactor y flujo nuevo

```text
Textual (cli/tui.py)
  → comandos independientes (cli/backend.py)
  → workflows / architect / plan runner / scheduler
  → workers / tools / providers
```

Los límites reales de ejecución emiten dataclasses `Event`: started, finished,
failed, message, status, project, branch, stream_start, delta, cancelled, done.
El callback opcional reside en un `ContextVar`; sin callback, los métodos
conservan sus resultados y excepciones. No se importan widgets en el motor.

Cada operación síncrona se ejecuta en un proceso `spawn` dedicado. Un pipe
transporta eventos; un timer de Textual los recibe sin bloquear. El proceso tiene
su propio grupo POSIX; Ctrl+C envía SIGINT al backend y a sus hijos Codex/pytest.
Se conserva el manejo de KeyboardInterrupt del scheduler. Repetir Ctrl+C no
interrumpe su rollback. Una solicitud anterior a la inicialización se entrega
cuando el backend está listo. Ctrl+Q espera la cancelación antes de salir.

La sección Git add/commit/guardado del checkpoint difiere SIGINT en la TUI para
permitir la reconciliación de un commit completado. La CLI clásica conserva
su comportamiento de señales. Una operación que no responda a SIGINT puede
seguir pendiente hasta su timeout: no se mata automáticamente un worker que
pueda estar modificando archivos.

Streaming HTTP/SSE local y SDK DeepSeek sólo se activa en el backend TUI;
se acumula exactamente la misma respuesta para los parsers existentes.
Los fragmentos JSON aparecen progresivamente dentro de un panel expandible
por respuesta, con detalle visible limitado a 24.000 caracteres. No se muestran
como prosa del agente. Codex/architect aún entregan su resultado al terminar.

Los prints de diagnóstico existentes se escriben al log del proceso, sin
interpretarlos ni utilizarlos para generar widgets. Los errores cortos y sus
tracebacks expandibles llegan por eventos. Los detalles de logs pueden contener
código y respuestas del modelo; el historial visible no se persiste.

## Archivos creados

- `cli/tui.py`: app, editor, conversación, actividades, selector y paleta.
- `cli/backend.py`: comandos y proceso cancelable, sin dependencia de Textual.
- `cli/projects.py`: validación, navegación, sugerencias y recientes.
- `orchestrator/events.py`: eventos opcionales, decorador y sección crítica.
- `requirements.txt`: dependencias explícitas del runtime.
- `tests/test_tui.py`: pruebas de comportamiento.
- `docs/TUI.md`: esta entrega.

## Archivos modificados

- `main.py`: TUI por defecto en una terminal; `--tui` y `--classic`.
- `cli/workflows.py`: resumen estructurado; conserva print en modo clásico.
- `orchestrator/architect.py`, `local_worker.py`, `codex_worker.py`,
  `reviewer.py`, `codex_reviewer.py`: telemetría en límites de ejecución.
- `orchestrator/scheduler.py`: sección commit/checkpoint protegida en la TUI.
- `tools/filesystem.py`, `git.py`, `tests.py`: eventos de operaciones reales.
- `providers/local.py`, `deepseek.py`: streaming optativo, sin cambiar prompts.
- `README.md`: inicio y referencia de la nueva interfaz.

## Dependencias e inicio

Python 3.10+ recomendado. Se añadieron Textual (`>=6.12,<7`) y platformdirs
(`>=4,<5`). `requirements.txt` incluye también python-dotenv, requests y openai,
que ya utilizaba el proyecto. Entorno verificado: Textual 6.12.0 y Python 3.14.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py             # TUI en terminal interactiva
.venv/bin/python main.py --tui       # TUI explícita
.venv/bin/python main.py --classic   # CLI anterior
```

Sin argumentos y con stdin/stdout redirigidos, se conserva automáticamente el
modo clásico. Los comandos de proveedor, `--goal` y `--resume` no cambian.

## Shortcuts y comandos

| Atajo | Acción |
| --- | --- |
| Enter en editor | Nueva línea |
| Ctrl+S | Enviar mensaje/comando |
| Ctrl+C | Cancelar operación; en reposo conserva sesión |
| Ctrl+Q | Salir después de cancelar una operación activa |
| Ctrl+O | Selector de proyecto |
| Ctrl+P | Paleta de comandos |
| Tab / Shift+Tab | Cambiar foco; en ruta, Tab completa sugerencia |
| ↑ / ↓ en selector | Seleccionar carpeta/sugerencia |
| Enter en lista | Entrar en carpeta |
| Alt+↑ en selector | Directorio padre |
| Ctrl+O en selector | Open this folder |
| Esc en selector | Cancelar |

El editor incluye selección, navegación y scroll propios de TextArea. Se usa
Ctrl+S como envío compatible con terminales que no distinguen Ctrl+Enter.

`/project` abre el selector; `/project <ruta>` selecciona directamente.
`/new <nombre>` abre el selector de carpeta padre; `/new <nombre> <padre>`
utiliza el flujo existente directamente. Se admiten comillas para espacios.
`/model [id]` consulta/cambia el ID local de la sesión, sin cambiar escalación
ni configuración persistida. `/context` explica que no hay métricas fiables.
`/status`, `/plan`, `/budget`, `/resume`, `/clear`, `/help`, `/quit`, `/exit`
conservan comportamiento real. `/git` y `/tests` llaman a GitManager/TestRunner.
La paleta utiliza esos mismos comandos; crear proyecto/cambiar modelo prepara
un comando editable en el input.

El selector permite navegar cualquier carpeta accesible; abrirla como proyecto
valida Git mediante el servicio existente. `~`, rutas absolutas y relativas
respetan el CWD. Los recientes guardan sólo ocho rutas absolutas, ordenadas y
sin duplicados, mediante reemplazo de archivo temporal. Ubicaciones típicas:

- macOS: `~/Library/Application Support/raphael/recent-projects.json`.
- Linux: `$XDG_CONFIG_HOME/raphael/recent-projects.json` o `~/.config/raphael/…`.
- Log: directorio de logs de `platformdirs.user_log_path('raphael')`,
  `backend.log`. No se ha añadido rotación automática.

## Validación y límites

Suite inicial e incremental: los **138 tests previos** continuaron pasando;
se mantuvo el único skip de conectividad DeepSeek optativa. Se añadieron **14
casos efectivos**: rutas absoluta/relativa/home, inexistente/archivo/permisos,
sugerencias, persistencia/corrupción/orden de recientes, eventos de filesystem,
streaming preservando JSON, editor >40.000 caracteres, picker con teclado,
comandos/paleta/eventos/borrador, proceso real de selección/error, cancelación
real de pytest y siguiente operación, interrupción diferida al guardar
checkpoint, tamaño 80×24 y entrypoints TUI/clásico.

Resultado final: **152 passed, 1 skipped** con `.venv/bin/python -m pytest -q`.
`compileall` y `git diff --check` completaron sin errores.

Se comprobó además inicio/salida real en pseudoterminal y compilación Python.
Las pruebas HTTP/modelos no consumen tokens. No se ejecutó una implementación
real con los providers remotos ni una validación en un host Linux.

Limitaciones preservadas/documentadas:

- Hay un único plan/checkpoint global. Cambiar proyecto no borra el plan;
  un objetivo nuevo se rechaza si existe un plan pendiente de otro proyecto.
- El historial es presentación; cada objetivo mantiene el flujo de planificación
  actual, no una conversación de chat persistente enviada al modelo.
- Context tokens y límite no están disponibles y no se inventan estimaciones.
- `/model` acepta un ID manual; no hay catálogo remoto ni verificación del ID.
- Actividades internas de Codex no se desglosan; architect/Codex no hacen streaming.
- El navegador consulta carpetas en threads para no bloquear la TUI, pero un
  filesystem de red lento puede tardar en contestar. No hay virtualización de
  directorios enormes ni límites globales al historial de widgets.
- La cancelación respeta la recuperación existente; en etapas sin rollback
  inmediato, `/resume` utiliza el checkpoint para recuperar. Un cierre forzado
  externo del proceso sigue requiriendo inspección/recuperación del plan.

Segunda iteración deliberada: streaming estructurado de Codex, catálogo de
modelos, conversación persistente en backend, planes por proyecto, métricas de
contexto con soporte real del provider, rotación de logs y virtualización de
historias/directorios muy grandes. Ninguna herramienta existente fue eliminada
ni se cambiaron tests previos para ocultar fallos.

## Corrección de apertura desde Textual

La validación inicial no cubría el primer spawn de multiprocessing desde una
TUI recién iniciada. Textual sustituye stderr por un proxy con descriptor -1;
el resource tracker de Python 3.14 intentaba heredarlo y fallaba con
`ValueError: bad value(s) in fds_to_keep`. El arranque ahora utiliza un stderr
con descriptor válido durante spawn, restaura el proxy y cierra los pipes si
falla. Los fallos de arranque se presentan dentro de la TUI.

También se corrigió la prioridad de Ctrl+O dentro del selector: abre la carpeta
actual. Dos pruebas en intérpretes nuevos verifican selección desde el picker
por teclado y mouse, guardado de recientes y ejecución posterior de /status.
Una tercera comprueba presentación de errores de arranque. Resultado después
de esta corrección: **155 passed, 1 skipped**, compileall y diff --check correctos.


## Diagnóstico y modelo de Codex

El log de la ejecución del usuario mostró rechazo HTTP 400 de `gpt-6.1-sol`
para su autenticación ChatGPT. El modelo provenía de la configuración global
de Codex, no del modelo local de Raphael. Una petición real mínima con
`codex exec --model gpt-6-sol --sandbox read-only --ephemeral` respondió OK.
Se añadió `CODEX_MODEL=gpt-6-sol` al .env local de este proyecto. No se modificó
la configuración global de Codex. Si CODEX_MODEL está vacío en otra instalación,
se conserva el modelo de su configuración Codex.

`/model codex <id>` permite cambiar el modelo Codex durante la sesión; el ID
se propaga al proceso y a architect/worker/reviewer. `/model <id>` sigue cambiando
sólo el modelo local. La barra muestra ambos. Los errores Codex extraen las
líneas ERROR del diagnóstico y destacan su mensaje antes del prompt repetido.
Los detalles de acciones fallidas permanecen colapsados hasta abrirlos.

Se añadieron cuatro casos efectivos de regresión para override opcional
compartido, mensaje de rechazo y propagación/presentación en TUI. La prueba
real verificó acceso al modelo; no ejecutó un plan de implementación completo.
La selección `--model` está documentada en la referencia oficial:
https://learn.chatgpt.com/docs/developer-commands?surface=cli

Resultado final tras esta corrección: **159 passed, 1 skipped**. Compilación
Python y `git diff --check` sin errores.

## Observabilidad por petición

Se corrigió una pérdida real de información: PlanRunner recibía TaskResult con
motivo/intentios/revisión, pero devolvía sólo un plan failed. Ahora emite
request_failed con tarea, causa, intentos, revisión y evidencia de tests antes
de marcarla fallida. El scheduler publica el provider seleccionado y sus
contadores; los rechazos de revisores generan actividad fallida (approved=False).
Los fallos de inicialización de logs también emiten error, no sólo done.

Cada operación tiene ID, timestamp UTC, proyecto, comando/prompt y modelos;
`cli/diagnostics.py` persiste esos datos y eventos en JSONL y asocia un log
`.log` propio. La ubicación predeterminada es el directorio de logs de Raphael,
subcarpeta requests. El registro contiene el prompt para poder recuperarlo;
puede contener código, salidas de tests y errores, y se conserva localmente.
No se registran deltas individuales del streaming. Los eventos guardan
resultados y marcas temporales independientemente del texto impreso por el motor.

- `/diagnostics`: panel con petición y eventos, scroll y Esc para cerrar.
- `/requests`: últimas 20 peticiones con ID y resultado.
- `/diagnostics <id>`: inspección de una petición anterior después de otras consultas.
- `/retry`: restaura el último objetivo como borrador; no llama al modelo.
  Sin historial de peticiones puede recuperar el goal del plan existente.
  Comprueba que el proyecto coincide; un plan pendiente continúa con `/resume`.
- La barra muestra tiempo transcurrido y conserva Failed al terminar un fallo.

Los registros permiten consultar fallos después de reiniciar la app. No se
puede reconstruir un motivo que versiones anteriores descartaron. En el estado
inspeccionado, el plan de dnd_test tenía task-001 fallida y el checkpoint disponible
pertenecía a otro proyecto; por eso no se atribuyó una causa a ese fallo histórico
ni se ejecutó automáticamente un objetivo o recuperación.

Pruebas nuevas: persistencia de causa/prompt/historial; emisión exacta de fallo
desde PlanRunner; panel/Failed/retry después de consultas; rechazo de revisor;
recuperación del objetivo de un plan previo sin journal; error al iniciar log.
Se actualizó la prueba de error de arranque para exigir Failed y registro durable,
conforme al nuevo comportamiento visible. No se desactivaron pruebas.

Validación final de observabilidad: **165 passed, 1 skipped**; compileall y
`git diff --check` sin errores. No se volvió a ejecutar el objetivo del proyecto.
