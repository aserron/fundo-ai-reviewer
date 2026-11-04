# 04 — Incertidumbre Residual y Escalación a Revisión Humana (HITL)

En suscripción de crédito, tan importante como corregir errores obvios es reconocer cuándo la descripción bancaria **no contiene suficiente información** para tomar una decisión automática segura. Este documento explica cómo el sistema detecta y escala esos casos.

---

## 1. ¿Qué es la Incertidumbre Residual?

Incluso después de aplicar reglas deterministas y un modelo de lenguaje (`gemini-2.5-flash`), existen transacciones cuyo memo bancario es intrínsecamente ambiguo:
- Una transferencia por **Venmo o Zelle de una persona física** sin número de factura ni concepto comercial.
- Una **transferencia cablegráfica (*wire*)** donde el banco receptor truncó el nombre del originador y el motivo del pago (`REF 994128 UNSPECIFIED`).

Si el sistema clasifica a ciegas esos ingresos con alta confianza (`confidence > 0.90`), corre dos riesgos opuestos:
1. **Riesgo de sobre-préstamo:** Contar un préstamo privado o ahorro familiar como venta recurrente (inflando la oferta en `$1.20` por cada `$1.00` mensual).
2. **Riesgo de rechazo injusto:** Descartar el pago real de un cliente que pagó por Venmo o transferencia bancaria.

---

## 2. Regla de Calibración y Compuerta de Escalación ([`src/guardrails.py`](../src/guardrails.py))

En [`build_batch_prompt`](../src/reviewer.py) y [`apply_post_guardrails`](../src/guardrails.py) implementamos un protocolo de dos pasos:

1. **Calibración en el Prompt del Reviewer:**
   Cuando el LLM encuentra un crédito grande (`< -$2,500`) correspondiente a un cobro peer-to-peer (`VENMO CASHOUT`) o un cable sin remitente (`WIRE INFLOW ... UNSPECIFIED`) sin referencia a factura o contrato, conserva `group = "None"`, `is_business = True` pero asigna **`confidence = 0.52`** (baja certeza).
2. **Compuerta Determinista en [`apply_post_guardrails`](../src/guardrails.py):**
   ```python
   is_large_ambiguous_credit = (
       tx.is_credit and tx.abs_amount >= 2500.0 and proposal.confidence < 0.70
   )
   ```
   - Para cualquier transacción menor o débito con `confidence < 0.75`, el guardrail **suprime** la bandera (`is_flagged = False`) para no saturar al equipo humano con ruido.
   - Pero cuando `is_large_ambiguous_credit == True`, el guardrail **fuerza `is_flagged = True`** (incluso si `original_group == proposed_group == "None"`) y antepone el aviso estándar de 5 segundos:
     `"Low-confidence large credit ($X,XXX.XX) — manual check: <explicación>"`

---

## 3. Los 2 Casos Auditables en el Dataset y en [`outputs/review_flags.jsonl`](../outputs/review_flags.jsonl)

### Caso 1: `tx_biz_03_0875` (`BIZ_03` — Metro Dental Studio)
- **Datos de entrada ([`data/transactions.jsonl`](../data/transactions.jsonl)):**
  - Fecha: `2026-02-14`
  - Concepto: `"VENMO CASHOUT FROM ALEX SMITH"`
  - Monto: **`-$3,200.00`**
- **Ambigüedad de negocio:**
  En un consultorio odontológico (`Healthcare`), `$3,200` coincide tanto con el valor de un tratamiento de ortodoncia/implante pagado por un paciente llamado Alex Smith como con una transferencia personal de un familiar del doctor.
- **Salida generada en [`outputs/review_flags.jsonl`](../outputs/review_flags.jsonl):**
  ```json
  {
    "transaction_id": "tx_biz_03_0875",
    "business_id": "BIZ_03",
    "original_group": "None",
    "proposed_group": "None",
    "is_business": true,
    "is_revenue": true,
    "confidence": 0.52,
    "reason": "Low-confidence large credit ($3,200.00) — manual check: Ambiguous peer-to-peer Venmo cashout, requires manual review.",
    "dollar_impact": 3200.0,
    "is_flagged": true
  }
  ```
- **Impacto potencial en la oferta si el analista humano lo rechaza:**
  Reduciría el ingreso mensual de `BIZ_03` en `$3,200 / 3 = $1,066.67/mes` y ajustaría la oferta en `-$1,280.00`.

---

### Caso 2: `tx_biz_07_1986` (`BIZ_07` — RedTeam Services LLC)
- **Datos de entrada ([`data/transactions.jsonl`](../data/transactions.jsonl)):**
  - Fecha: `2026-02-05`
  - Concepto: `"WIRE INFLOW REF 994128 UNSPECIFIED"`
  - Monto: **`-$4,500.00`**
- **Ambigüedad de negocio:**
  En una firma de consultoría (`Professional Services`), los clientes suelen pagar por wire, pero el banco truncó el remitente dejando solo `REF 994128 UNSPECIFIED`. Además, `BIZ_07` es un solicitante que intentó inyectar prompts en otros memos, por lo que cualquier entrada grande sin remitente exige comprobante documental.
- **Salida generada en [`outputs/review_flags.jsonl`](../outputs/review_flags.jsonl):**
  ```json
  {
    "transaction_id": "tx_biz_07_1986",
    "business_id": "BIZ_07",
    "original_group": "None",
    "proposed_group": "None",
    "is_business": true,
    "is_revenue": true,
    "confidence": 0.52,
    "reason": "Low-confidence large credit ($4,500.00) — manual check: Unspecified wire inflow, requires manual underwriter review.",
    "dollar_impact": 4500.0,
    "is_flagged": true
  }
  ```
- **Impacto potencial en la oferta si el analista humano lo rechaza:**
  Reduciría el ingreso mensual de `BIZ_07` en `$4,500 / 3 = $1,500.00/mes` y ajustaría la oferta en `-$1,800.00`.

---

## 4. Flujo de Trabajo del Analista Humano (*Human-in-the-Loop Flywheel*)

En producción, las banderas de `review_flags.jsonl` no se muestran en orden cronológico, sino ordenadas por **Impacto en la Decisión de Crédito**:
1. **Prioridad 1 — Cruce del Acantilado NSF (`NSF = 5` $\leftrightarrow$ `NSF >= 6`):** Banderas sobre eventos `NSFs` / `Overdraft` en comercios que están a 1 o 2 eventos del rechazo total (`BIZ_04`, `BIZ_05`).
2. **Prioridad 2 — Créditos de Incertidumbre Residual y Desembolsos de Préstamos (`dollar_impact >= $2,500`):** Donde un solo clic del analista (pedir factura o excluir el depósito) mueve la oferta en miles de dólares (`BIZ_01`, `BIZ_03`, `BIZ_06`, `BIZ_07`).
3. **Prioridad 3 — Series de Débitos Recurrentes de MCA (`Active advance`):** Agrupadas por contraparte (ej. confirmar de un vistazo la serie de 64 débitos de `SQUARE CAPITAL PAYMENT` en vez de revisar 64 filas por separado).
