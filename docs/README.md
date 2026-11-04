# Documentación Detallada — Fundo AI Reviewer

Esta carpeta desglosa el funcionamiento técnico, las reglas financieras y la interpretación de los resultados del sistema híbrido de auditoría de transacciones bancarias.

## Índice de Documentos

1. **[01 — Glosario de Dominio, Signos de Plaid y Fórmula de Crédito](01-domain-and-formulas.md)**
   Define los conceptos de suscripción de crédito (*underwriting*): MCA, NSF, códigos NACHA `R01`/`R09`, la convención de signos de Plaid (`amount < 0` ingreso vs `amount > 0` egreso), las 13 categorías de `FundoGroup`, la invariante de ingresos y la fórmula matemática de la oferta de préstamo.

2. **[02 — Recorrido Paso a Paso del Pipeline Híbrido](02-pipeline-walkthrough.md)**
   Explica el flujo completo de una transacción desde el JSON crudo hasta la oferta final: sanitización contra *Prompt Injection*, las 3 fallas del motor Legacy, el filtro determinista *Pre-LLM Triage Gate*, la deduplicación canónica SHA-256 en SQLite WAL (de 816 candidatos a 88 consultas a Gemini 2.5 Flash) y los *Post-LLM Guardrails* (`Sign Lock`, `Formula Lock`, `Confidence Gate`).

3. **[03 — Guía de Columnas y Resultados por Negocio (`BIZ_01` a `BIZ_10`)](03-portfolio-results-by-business.md)**
   Explica qué significa cada columna de la tabla de evaluación (`Legacy Offer`, `Reviewed Offer`, `Ground Truth`, `Legacy Rev MAE`, `Reviewed Rev MAE`) y detalla qué ocurrió exactamente en cada uno de los 10 comercios del portafolio.

4. **[04 — Incertidumbre Residual y Escalación a Revisión Humana (HITL)](04-residual-uncertainty-and-hitl.md)**
   Documenta cómo actúa el sistema cuando un memo bancario carece de contexto suficiente para decidir con certeza (`tx_biz_03_0875` y `tx_biz_07_1986`), cómo opera el umbral `confidence < 0.70` en créditos mayores a `$2,500` y cómo se prioriza la cola del analista humano.

5. **[05 — Simulación Monte Carlo (4 Modos) y Riesgos de Ingesta](05-sensitivity-and-risk-analysis.md)**
   Analiza los resultados de perturbar el 2%, 5% y 10% de las etiquetas en 4 modos aislados (`random_all_categories`, `false_revenue_only`, `false_active_advance_only`, `false_nsf_only`), más las soluciones de ingeniería para neobancos sin cargos NSF y extractos truncados de 61 días.
