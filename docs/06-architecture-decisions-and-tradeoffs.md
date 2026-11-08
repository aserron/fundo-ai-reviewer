# 06 — Architecture Decision Records (ADRs) & Key Tradeoffs

Este documento registra las decisiones arquitectónicas centrales del sistema **Fundo AI Reviewer** en formato **ADR (Architecture Decision Record)**. Cada decisión está anclada en los requerimientos explícitos del challenge: ejecución reproducible en un comando, presupuesto de API `< $10 USD`, rechazo al *over-engineering* cosmético y foco estricto en la lógica financiera de suscripción de crédito (*underwriting*).

---

## Resumen Ejecutivo de Tradeoffs

| ADR | Decisión Arquitectónica | Driver Principal (Letra / Negocio) | Qué ganamos (Pros) | Qué sacrificamos / Riesgo aceptado (Cons) |
| :--- | :--- | :--- | :--- | :--- |
| **ADR-001** | **Zero-Framework & Mínimas Dependencias Externas** (`pydantic`, `sqlite3`, `rich`, SDK nativo) | Descartar abstracciones sin justificación funcional; reproducibilidad inmediata (`uv sync`) | Arranque instantáneo, cero rotura por versiones de wrappers, control exacto de prompts y reintentos | Reimplementación manual de batching, parseo defensivo JSON y backoff exponencial en [`src/reviewer.py`](../src/reviewer.py) |
| **ADR-002** | **Sin Autenticación, Login ni RBAC** (Ejecución directa por CLI local) | Demo reproducible en 1 comando (`--use-cache` sin API key); foco en lógica de negocio | Cero fricción para el evaluador; ejecución 100% automatizable en CI y en vivo | Inexistente control de acceso multi-usuario o auditoría de identidad (requiere API Gateway/IAM si pasa a servicio interno) |
| **ADR-003** | **Sin Interfaz Gráfica (GUI/Web); CLI Minimalista + JSON/JSONL** | Requerimiento minimalista; foco en métricas de crédito y explicaciones de 5 segundos | Tiempo de ingeniería 100% invertido en invariantes financieras, simulación Monte Carlo y tests | Sin botones interactivos para el *1-click override* del analista humano; la inspección visual depende de terminal o JSONL |
| **ADR-004** | **Persistencia Dual Embebida (SQLite WAL + JSONL Seed)** en vez de Postgres/Redis/Docker | Correr en pocos comandos sin levantar demonios ni contenedores externos | Replay offline en `< 3.5s` con `$0.00` de gasto; trazabilidad en Git mediante diff de texto plano | Concurrencia limitada a un nodo local; colisión de claves si se versionan prompts sin migrar el archivo semilla |
| **ADR-005** | **Pipeline Híbrido (Pre-Triage + Deduplicación Canónica + Post-Guardrails)** vs. Pure LLM | Presupuesto `< $10 USD`; prohibición de violar invariantes contables (ej. débito = ingreso) | Reducción de 2,520 txs a 88 llamadas reales (`$0.0030 USD` total); 0% de violaciones de signo o fórmula | Falsos negativos potenciales si aparece un procesador con un patrón léxico inédito que no cruce el umbral de `$400` ni sea recurrente |
| **ADR-006** | **Datos Sintéticos Deterministas (`seed=42`) con `_ground_truth` Embebido** | Ausencia de datos bancarios reales por PII; necesidad de medir MAE exacto y casos borde | Cobertura reproducible de 10 arquetipos de fallo (inyección, neobancos R01, 61 días, *hard negatives*) | El generador y las reglas de evaluación nacen del mismo diseño de dominio; riesgo de sobreajuste a los 10 arquetipos conocidos |

---

## ADR-001: Minimización de Dependencias Externas y Arquitectura Zero-Framework

* **Estado:** Aceptado
* **Requisitos vinculados:** *Simplicity first*; descartar cualquier librería, arquitectura o patrón que solo busque hacer el código más "elegante" o modular sin impacto real en el problema.
* **Contexto:**
  En proyectos con LLMs es habitual importar frameworks como LangChain, LlamaIndex, Instructor, Celery o bases vectoriales externas. Sin embargo, para auditar lotes de transacciones bancarias con un esquema fijo de 13 categorías (`FundoGroup`), estas capas añaden megabytes de dependencias transitivas, ocultan el prompt real enviado al modelo y dificultan la depuración cuando el LLM devuelve JSON malformado.
* **Decisión:**
  Construir el sistema únicamente sobre la librería estándar de Python 3.12 (`sqlite3`, `hashlib`, `re`, `argparse`, `statistics`) y cuatro paquetes directos en [`pyproject.toml`](../pyproject.toml): `pydantic` (validación de esquemas), `google-genai` / `openai` (clientes HTTP oficiales), `rich` (tablas en consola) y `pytest`. La abstracción entre proveedores se resuelve con un `Protocol` de 10 líneas ([`LLMProvider`](../src/reviewer.py)).
* **Consecuencias:**
  * **Pros:**
    * Instalación determinista en segundos vía `uv sync` sin conflictos de compilación en Windows, macOS o Linux.
    * Transparencia total sobre el prompt exacto ([`build_batch_prompt`](../src/reviewer.py)) y el cálculo de tokens/costos ([`ReviewCacheService`](../src/cache_store.py)).
    * Superficie de ataque y mantenimiento mínima.
  * **Cons (Costo asumido):**
    * Tuvimos que programar manualmente la lógica de reintentos con *exponential backoff*, la partición de lotes (*chunking* de a 20 transacciones) y el parseo defensivo ante respuestas envueltas en diccionarios o listas posicionales ([`parse_llm_proposals`](../src/reviewer.py)).

---

## ADR-002: Ausencia de Login, Password, Sesiones y Gestión de Usuarios

* **Estado:** Aceptado
* **Requisitos vinculados:** Ejecución inmediata en uno o pocos comandos; evaluación centrada en el motor de revisión y su impacto crediticio, no en infraestructura de autenticación.
* **Contexto:**
  En un entorno bancario real, un sistema que consulta transacciones de Plaid y modifica ofertas de crédito requiere autenticación SSO/OAuth2, control de acceso basado en roles (RBAC) y cifrado de secretos en tránsito. Implementar un sistema de login/password o tokens JWT en un *take-home challenge* agregaría pasos manuales (crear usuarios, iniciar sesión, configurar variables de sesión) que contradicen la consigna de evaluación rápida.
* **Decisión:**
  Omitir por completo cualquier capa de autenticación de usuarios. El sistema confía en el entorno de ejecución local del operador y lee opcionalmente `GEMINI_API_KEY` u `OPENAI_API_KEY` desde variables de entorno o `.env` mediante un cargador liviano sin dependencias ([`load_env_file`](../src/cli.py)). En modo `--use-cache`, ni siquiera requiere credenciales de API.
* **Consecuencias:**
  * **Pros:**
    * **Reproducibilidad Zero-Key:** El evaluador clona el repositorio y ejecuta `uv run python -m src.cli run --use-cache` obteniendo todas las métricas en `< 3.5 segundos` sin registrarse ni configurar claves.
    * Elimina código muerto de gestión de sesiones, hashing de contraseñas y middlewares de autorización que no aportan al cálculo de riesgo crediticio.
  * **Cons (Costo asumido):**
    * Cualquier proceso con acceso al sistema de archivos local puede leer o sobrescribir `cache/llm_cache.db` y `outputs/review_flags.jsonl`.
    * No registra *quién* ejecutó una corrida ni *qué analista* aprobó un cambio (en producción esto se delega al API Gateway corporativo y a la tabla inmutable `underwriting_decision_snapshots`).

---

## ADR-003: Sin Interfaz Gráfica (GUI / Web App) y CLI Ultra-Básica

* **Estado:** Aceptado
* **Requisitos vinculados:** Minimalismo arquitectónico; foco estricto en los requerimientos reales del negocio (*underwriting accuracy*, sensibilidad al error y explicabilidad en 5 segundos).
* **Contexto:**
  Un sistema de revisión *Human-in-the-Loop (HITL)* suele visualizarse mediante un dashboard web (React/Next.js + FastAPI) donde el analista filtra transacciones, inspecciona gráficos y hace clic en botones de aprobación. Construir un frontend web y una API REST consumiría más del 50% del tiempo de desarrollo en maquetación, estado de UI y ruteo HTTP, restando profundidad al motor de reglas y al análisis matemático de riesgo.
* **Decisión:**
  Exponer toda la funcionalidad a través de una interfaz de línea de comandos (`argparse` en [`src/cli.py`](../src/cli.py)) con tres subcomandos (`run`, `review-file`, `cache-stats`), tablas resumidas en terminal (`rich`) y artefactos estructurados en disco (`outputs/review_flags.jsonl`, `outputs/business_metrics.json`, `outputs/sensitivity_report.json`).
* **Consecuencias:**
  * **Pros:**
    * El 100% del esfuerzo de ingeniería se concentró en los problemas difíciles del dominio: neutralización de *prompt injection*, detección de códigos NACHA `R01`/`R09` en neobancos, normalización de ventanas truncadas de 61 días y simulación Monte Carlo en 4 modos.
    * El subcomando `review-file` permite auditar en vivo cualquier archivo JSON/JSONL externo que el entrevistador entregue durante el *debrief* sin levantar servidores web ni puertos locales.
    * Los archivos `.jsonl` y `.json` generados son directamente consumibles por scripts de evaluación automática (`jq`, `pytest`).
  * **Cons (Costo asumido):**
    * No existe una cola visual interactiva para que el suscriptor humano ejecute el *1-click override*; el ciclo de retroalimentación (*HITL flywheel*) está diseñado a nivel de contrato de datos y arquitectura, pero su interfaz actual es la consola y los archivos de salida.

---

## ADR-004: Caché Embebida SQLite WAL + Espejo JSONL vs. Infraestructura Externa (Docker / Postgres / Redis)

* **Estado:** Aceptado
* **Requisitos vinculados:** Correr sin servicios externos; mantener el gasto de LLM muy por debajo de `$10 USD`; garantizar determinismo entre corridas.
* **Contexto:**
  Si cada ejecución del pipeline o de los tests llamara en vivo a la API del LLM para las 2,520 transacciones, la corrida tardaría minutos, gastaría presupuesto en cada test y produciría pequeñas variaciones estocásticas. Usar Redis o PostgreSQL en Docker obligaría al evaluador a tener Docker Desktop activo y levantar contenedores antes de probar el código.
* **Decisión:**
  Implementar [`ReviewCacheService`](../src/cache_store.py) sobre SQLite embebido en modo `WAL` (`cache/llm_cache.db`) sincronizado automáticamente con un archivo de texto plano versionado en Git (`cache/llm_cache_seed.jsonl`). Si `llm_cache.db` no existe o está vacío, se auto-hidrata desde el `.jsonl` al iniciar.
* **Consecuencias:**
  * **Pros:**
    * Cero demonios en segundo plano: funciona como un archivo local dentro del proceso de Python.
    * **Auditabilidad en Git:** Mientras que un binario SQLite es opaco en un Pull Request, `cache/llm_cache_seed.jsonl` permite revisar línea por línea en GitHub qué decidió `gemini-2.5-flash`, cuántos tokens consumió y cuánto costó cada lote (`$0.0030 USD` total).
  * **Cons (Costo asumido):**
    * SQLite local no sirve para múltiples workers distribuidos escribiendo en paralelo desde distintos contenedores en producción.
    * Acoplamiento a la firma de caché: cualquier cambio en `prompt_version` o en la lógica de `make_cache_key` invalida los 88 registros pre-calculados y requiere una corrida con API key viva para regenerar la semilla.

---

## ADR-005: Pipeline Híbrido con Pre-Triage y Deduplicación Canónica vs. Clasificación 100% LLM

* **Estado:** Aceptado
* **Requisitos vinculados:** Delimitación explícita *Code vs. Model Boundary*; invariantes contables estrictas; eficiencia de costos y latencia.
* **Contexto:**
  Enviar las 2,520 transacciones completas a un LLM y confiar directamente en su salida presenta tres fallas críticas:
  1. El LLM puede violar reglas duras del negocio (ej. marcar un débito positivo como ingreso o calcular mal la fórmula de ingreso mensual).
  2. El 67.6% de las transacciones son débitos operativos rutinarios (gasolina, insumos, luz) que no alteran el ingreso ni los NSFs.
  3. Las liquidaciones diarias de tarjetas (`*SQ JOES TACOS SETTLE 230`, `SETTLE 841`) se repiten decenas de veces por comercio variando solo el número de lote y unos pocos dólares.
* **Decisión:**
  Encapsular al LLM entre dos capas deterministas en [`src/guardrails.py`](../src/guardrails.py) y [`src/cache_store.py`](../src/cache_store.py):
  1. **Pre-LLM Triage Gate (`should_route_to_llm`):** Filtra automáticamente 1,704 débitos rutinarios limpios y solo enruta los 816 candidatos con riesgo asimétrico (todos los créditos, colisiones de reglas, tokens ambiguos, montos `> $400` sin categoría y débitos idénticos recurrentes `3+` veces).
  2. **Deduplicación Canónica (`make_cache_key`):** Enmascara números de secuencia de 3+ dígitos (preservando `941`, `R01`, `R09`) y agrupa montos en tramos (`<50`, `50-250`, `250-1000`, etc.), colapsando las 816 transacciones candidatas en **88 firmas únicas** evaluadas por el LLM.
  3. **Post-LLM Guardrails (`apply_post_guardrails`):** Fuerza `is_revenue = False` en todo débito (`Sign Lock`), recalcula `compute_is_revenue` en Python (`Formula Lock`), suprime discrepancias con `confidence < 0.75` y escala créditos grandes ambiguos (`>= $2,500` con `confidence < 0.60`) a revisión manual.
* **Consecuencias:**
  * **Pros:**
    * Reducción del 96.5% en llamadas al modelo (de 2,520 transacciones a 88 evaluaciones canónicas), logrando un gasto total de **`$0.0030 USD`** (3,300 veces por debajo del límite de `$10 USD`).
    * Garantía matemática del 100% sobre las invariantes de signo de Plaid y la fórmula de oferta, incluso si el LLM alucina o sufre un ataque de *prompt injection*.
  * **Cons (Costo asumido):**
    * **Riesgo de punto ciego en el Pre-Triage:** Si un comercio tiene un débito de prestamista MCA con un nombre inédito (no incluido en `AMBIGUOUS_TOKENS`), por un monto menor a `$400` y con cobros variables (no recurrentes exactos), el filtro `should_route_to_llm` lo auto-aceptará como gasto normal sin consultarle al LLM.
    * **Pérdida de granularidad por *bucketing*:** Dos transacciones del mismo proveedor dentro del mismo rango de monto (ej. `$1,200` y `$4,800`, ambas en el bucket `1000-5000`) comparten la misma decisión cacheada del LLM.

---

## ADR-006: Dataset Sintético Determinista (`seed=42`) con Ground Truth Embebido vs. Datos Reales Anonimizados

* **Estado:** Aceptado
* **Requisitos vinculados:** Generación de ~2,200+ transacciones realistas en 10 comercios; capacidad de demostrar cuantitativamente la reducción de error (MAE) y la sensibilidad al 2%, 5% y 10%.
* **Contexto:**
  No es posible distribuir extractos bancarios reales de PyMEs en un repositorio público por regulaciones de privacidad financiera (PII / GLBA). Además, los datos crudos reales rara vez incluyen etiquetas *ground truth* auditadas al 100% ni cubren de forma controlada todos los casos extremos requeridos por el challenge (inyección de prompts en memos, neobancos sin comisiones NSF, historial truncado a 61 días).
* **Decisión:**
  Construir un generador sintético determinista ([`src/generate_data.py`](../src/generate_data.py) con `random.Random(42)`) que produce 2,520 transacciones sobre 10 arquetipos de negocio (`BIZ_01` a `BIZ_10`), inyectando la etiqueta real en el campo privado `_ground_truth` de cada transacción. El motor Legacy, el Triage, el `FakeLLMProvider` y el `ReviewOrchestrator` tienen **estrictamente prohibido** leer `_ground_truth`; ese campo solo es leído por [`src/credit_impact.py`](../src/credit_impact.py) y [`src/cli.py`](../src/cli.py) al final del pipeline para medir el MAE y correr la simulación Monte Carlo.
* **Consecuencias:**
  * **Pros:**
    * Permite verificar con exactitud matemática que el error medio absoluto (MAE) de ingresos baja de `$9,855.27/mes` a `$0.00/mes` en el portafolio de prueba.
    * Garantiza que cada modo de fallo del motor Legacy (colisión de `TRANSFER` en Shopify, sobrecarga de `SQUARE CAPITAL`, fallo de regex `\b` en `*SQ` y `A+`, rebotes `R01`, ventana de 61 días) esté representado y cubierto por tests unitarios.
  * **Cons (Costo asumido):**
    * **Riesgo de sobreajuste (*Overfitting* al generador):** Al haber diseñado tanto el generador sintético como el prompt del revisor y las heurísticas de validación, el 100% de reducción de MAE refleja un dominio cerrado. En producción con datos salvajes de Plaid, el rendimiento real dependerá de descripciones bancarias truncadas o contrapartes regionales no vistas (razón por la cual se incluyeron los 2 casos de incertidumbre residual `tx_biz_03_0875` y `tx_biz_07_1986` y la arquitectura de *Shadow Mode* con monitoreo PSI en `SOLUTION.md`).
