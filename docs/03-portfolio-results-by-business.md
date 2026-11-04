# 03 — Guía de Columnas y Resultados por Negocio (`BIZ_01` a `BIZ_10`)

Este documento explica cómo leer la tabla principal de resultados (generada a partir de [`outputs/business_metrics.json`](../outputs/business_metrics.json)) y detalla qué ocurrió en cada uno de los 10 comercios del dataset.

---

## 1. Tabla Resumen del Portafolio

| Business | Industria | Legacy Offer | Reviewed Offer | Ground Truth | Legacy Rev MAE | Reviewed Rev MAE | Flags |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **`BIZ_01`** | Restaurant | `$70,583.92` | `$56,940.84` | `$56,940.84` | `$8,333.33` | **`$0.00`** | 155 |
| **`BIZ_02`** | E-Commerce | `$0.00` | `$64,617.78` | `$64,617.78` | `$55,817.38` | **`$0.00`** | 154 |
| **`BIZ_03`** | Healthcare | `$51,323.36` | `$47,723.36` | `$47,723.36` | `$3,000.00` | **`$0.00`** | 13 |
| **`BIZ_04`** | Trucking | `$69,964.29` | `$0.00` | `$0.00` | `$0.00` | **`$0.00`** | 2 |
| **`BIZ_05`** | Coffee Shop | `$36,581.04` | `$0.00` | `$0.00` | `$53.34` | **`$0.00`** | 16 |
| **`BIZ_06`** | Auto Repair | `$74,916.91` | `$64,959.98` | `$64,959.98` | `$6,000.00` | **`$0.00`** | 103 |
| **`BIZ_07`** | Professional Services | `$49,432.19` | `$35,632.19` | `$35,632.19` | `$11,500.00` | **`$0.00`** | 7 |
| **`BIZ_08`** | Wholesale | `$96,273.86` | `$105,605.86` | `$105,605.86` | `$7,776.67` | **`$0.00`** | 10 |
| **`BIZ_09`** | Manufacturing | `$67,032.50` | `$67,032.50` | `$67,032.50` | `$0.00` | **`$0.00`** | 1 |
| **`BIZ_10`** | Landscaping | `$15,326.51` | `$22,612.89` | `$22,612.89` | `$6,071.98` | **`$0.00`** | 0 |
| **Promedio / Total** | — | — | — | — | **`$9,855.27`** | **`$0.00`** | **461** |

---

## 2. Qué significa cada columna

1. **`Legacy Offer`:** El préstamo calculado usando únicamente las etiquetas del motor viejo de palabras clave ([`LegacyKeywordEngine`](../src/keyword_engine.py)) y dividiendo los ingresos siempre entre `3.0` meses.
2. **`Reviewed Offer`:** El préstamo calculado después de que nuestro pipeline híbrido audita las transacciones sospechosas y normaliza la ventana de días.
3. **`Ground Truth`:** El préstamo real que debería otorgarse según las etiquetas verdaderas (`_ground_truth`) del generador de datos. Cuando `Reviewed Offer == Ground Truth`, el error de oferta (`offer_dollar_error`) es `$0.00`.
4. **`Legacy Rev MAE` (`monthly_revenue_error_legacy`):** Cuántos dólares por mes de diferencia hay entre el ingreso mensual estimado por el motor Legacy y el ingreso mensual verdadero:
   $$\left|\text{avg\_monthly\_revenue}_{\text{legacy}} - \text{avg\_monthly\_revenue}_{\text{ground\_truth}}\right|$$
5. **`Reviewed Rev MAE` (`monthly_revenue_error_reviewed`):** Cuántos dólares por mes de diferencia hay entre el ingreso mensual estimado por nuestro Reviewer y el ingreso mensual verdadero (`$0.00` en los 10 comercios).
6. **`Flags` (`flagged_transactions_count`):** Cantidad de transacciones en ese negocio donde el Reviewer detectó un error del motor Legacy o una duda de baja confianza y emitió un registro en [`outputs/review_flags.jsonl`](../outputs/review_flags.jsonl).

---

## 3. Explicación Detallada de los 10 Negocios

### `BIZ_01` — Joe's Tacos (Restaurante — Sobrecarga de "SQUARE CAPITAL")
- **Problema:** El comercio procesa ventas con Square (`*SQ JOES TACOS SETTLE`, `TOAST`), pero también recibió un préstamo de **`-$25,000.00`** (`ACH CREDIT SQUARE CAPITAL LOAN PROCEEDS`) y paga **`+$185.00/día`** (`SQUARE CAPITAL PAYMENT`, 64 débitos = `$11,840`).
- **Fallo Legacy:** Al ver `"SQUARE"`, clasificó el préstamo de `$25,000` como ingreso (`+$8,333.33/mes` de inflación: `$58,819.93` vs `$50,486.60` reales) y clasificó los 64 cobros diarios de `$185` como `Revenue verification` en vez de `Active advance` (`other_funders_daily_payment = $0.00` vs `$182.15/día` reales). Además ignoró 90 liquidaciones con prefijo `*SQ`.
- **Corrección Reviewer (155 flags):** Reclasificó el préstamo de `$25,000` a `Not average monthly revenue` (`-$10,000.00` en la oferta) y los 64 débitos de `$185` a `Active advance` (`-$3,643.08` en la oferta), reduciendo la oferta de **`$70,583.92`** a **`$56,940.84`**.

### `BIZ_02` — Apex Apparel (E-Commerce — Colisión `"SHOPIFY"` vs `"TRANSFER"`)
- **Problema:** Las 90 liquidaciones de ventas de Shopify tienen el formato bancario `ORIG CO NAME:SHOPIFY CO ENTRY DESCR:TRANSFER` (totalizando `-$167,452.15`) y el negocio paga `+$120.00/día` a `SHOPIFY CAPITAL DAILY SPLIT DEBIT` (64 débitos).
- **Fallo Legacy:** Como `Internal transfer` (`"TRANSFER"`) se evalúa antes que `Revenue verification` (`"SHOPIFY"`), el motor Legacy etiquetó las 90 ventas como transferencias internas (`avg_monthly_revenue = $0.00`, error de **`$55,817.38/mes`**) y ofreció **`$0.00`**.
- **Corrección Reviewer (154 flags):** Restauró las 90 liquidaciones a `Revenue verification` (`$55,817.38/mes`) y reclasificó los 64 cobros de `$120` a `Active advance` (`$118.15/día`), habilitando una oferta legítima de **`$64,617.78`**.

### `BIZ_03` — Metro Dental Studio (Salud — Transferencias del Dueño, Gastos Personales y Venmo Ambiguo)
- **Problema:** El dueño hizo 12 transferencias desde su caja de ahorro personal. En 6 de ellas el concepto fue `ZELLE FROM DR METRO PERSONAL SAVINGS` (`-$1,500` c/u = `-$9,000` en 90 días), omitiendo la palabra `"TRANSFER"`. Además hizo 6 compras personales (`LOUIS VUITTON BOUTIQUE`, `EQUINOX GYM MEMBERSHIP`) y recibió un Venmo ambiguo de `-$3,200.00` (`VENMO CASHOUT FROM ALEX SMITH`, `tx_biz_03_0875`).
- **Fallo Legacy:** Contó los `-$9,000` del dueño como ingresos de la clínica (`+$3,000.00/mes` de error: `$42,769.47` vs `$39,769.47` reales), inflando la oferta en `+$3,600.00` (`$51,323.36` vs `$47,723.36`).
- **Corrección Reviewer (13 flags):** Reclasificó las 6 transferencias del dueño como `Internal transfer` (`is_business = False`), marcó las 6 compras de lujo/gimnasio como gastos personales (`is_business = False`), y elevó el Venmo de `$3,200` (`tx_biz_03_0875`) a revisión humana con `confidence = 0.52`.

### `BIZ_04` — High-Roller Freight LLC (Transporte — Acantilado `NSF > 5` y Apilamiento MCA)
- **Problema:** La empresa paga 3 préstamos MCA simultáneos (`ONDECK`, `CREDIBLY`, `FORWARD FINANCING` = `$349.54/día`), hace apuestas deportivas (`DRAFTKINGS`) y tuvo **7 eventos de NSF**: 5 escritos como `NSF FEE` y 2 escritos como `NSF/RET FEE`.
- **Fallo Legacy:** Detectó solo 5 NSFs (`nsf_overdraft_count = 5`). Como la regla de rechazo exige `NSF > 5`, el motor Legacy aprobó una oferta de **`$69,964.29`**.
- **Corrección Reviewer (2 flags):** Detectó los 2 `NSF/RET FEE` faltantes, llevando `nsf_overdraft_count` de `5` a **`7`**. Eso activó el rechazo automático y redujo la oferta a **`$0.00`**. *(El `Legacy Rev MAE` era `$0.00` porque los ingresos estaban bien calculados; el fallo mortal estaba en el conteo de NSFs)*.

### `BIZ_05` — CloudNine Coffee (Cafetería — Neobanco sin Comisiones NSF)
- **Problema:** Usa un neobanco que no cobra multas de `$35` (`bank_charges_nsf_fees = False`). Tuvo **8 rebotes de ACH** por falta de fondos con monto `$0.00` (`ACH RETURN R01 VENDOR PYMT BOUNCE` y `ACH RET R09 UNCOLLECTED PAYROLL RETRY`) y 8 micro-transferencias de rescate del dueño (`ZELLE FROM OWNER EMERGENCY CASH`, `-$20.00` c/u = `-$160.00`).
- **Fallo Legacy:** Como los memos de rebote no contenían la palabra `"NSF"` ni `"INSUFFICIENT FUNDS"`, contó `nsf_overdraft_count = 0` y además sumó los `$160` de rescate como ventas (`$53.34/mes` de error), ofreciendo **`$36,581.04`**.
- **Corrección Reviewer (16 flags):** Clasificó los 8 rebotes `R01`/`R09` como `NSFs` (`nsf_overdraft_count = 8 > 5`) y las 8 transferencias de `$20` como `Internal transfer`, llevando la oferta a **`$0.00`**.

### `BIZ_06` — Precision Auto Body (Taller Mecánico — Bug de Puntuación `*SQ` y `A+`)
- **Problema:** Tiene 42 cobros legítimos con símbolos (`*SQ`, `S&P`, `A+`), pero también recibió un adelanto de capital de **`-$18,000.00`** (`*SQ CAPITAL EQUIP ADVANCE`) y paga **`+$140.00/día`** a un prestamista (`A+ FUNDING DAILY ACH`, 60 débitos = `$8,400.00`).
- **Fallo Legacy:** El patrón `\b(?:\*SQ|A\+)\b` falló por los símbolos `*` y `+`, dejando todo en `None`. Como consecuencia, el adelanto de `-$18,000` se contó como venta (`+$6,000.00/mes` de error) y los `$140/día` de MCA fueron ignorados (`$0.00/día` vs `$137.85/día` reales), inflando la oferta a **`$74,916.91`**.
- **Corrección Reviewer (103 flags):** Clasificó las 42 ventas en `Revenue verification`, excluyó el préstamo de `-$18,000` (`Not average monthly revenue`) y registró los 60 pagos de `A+ FUNDING` como `Active advance`, ajustando la oferta a **`$64,959.98`**.

### `BIZ_07` — RedTeam Services LLC (Servicios Profesionales — 6 Ataques de *Prompt Injection*)
- **Problema:** Contiene 6 transacciones con instrucciones maliciosas inyectadas en el memo bancario:
  - 3 créditos que intentan forzar `REVENUE_VERIFICATION` en transferencias de socios y cables sin respaldo (`-$10,000`, `-$12,000`, `-$7,500`, `-$5,000`, sumando `-$34,500` = `+$11,500.00/mes` de ingresos falsos).
  - 2 débitos de MCA (`ONDECK`, `LIBERTAS`) que intentan hacerse pasar por gastos de luz/utilidades, y 1 cheque devuelto (`RETURNED CHECK`) que pide no contarse como NSF.
  - 1 cable ambiguo no malicioso (`WIRE INFLOW REF 994128 UNSPECIFIED`, `-$4,500.00`, `tx_biz_07_1986`).
- **Fallo Legacy:** Cayó en la trampa de los créditos no etiquetados, inflando el ingreso mensual en **`$11,500.00/mes`** y la oferta en **`+$13,800.00`** (`$49,432.19` vs `$35,632.19`).
- **Corrección Reviewer (7 flags):** Neutralizó todas las inyecciones, excluyó los `$34,500` de ingresos falsos, contó el cheque devuelto como `NSFs` (`nsf_overdraft_count = 1`) y elevó el wire `tx_biz_07_1986` a revisión humana con `confidence = 0.52`.

### `BIZ_08` — Summit Industrial Supply (Mayorista B2B — "Hard Negatives")
- **Problema:** Tiene 35 cobros de facturas de clientes corporativos cuyos nombres incluyen palabras gatillo:
  - 25 contienen `"CAPITAL"` o `"ADVANCE"` (`CAPITAL BUILDING GROUP INV 882`, `ADVANCE AUTO PARTS VENDOR CREDIT`, `LIBERTY CAPITAL COMMERCIAL REPAIR`).
  - 10 contienen `"TRANSFER"` (`NATIONAL TRANSFER WAREHOUSING CLIENT PMT`, `FIRST NATIONAL TITLE TRANSFER FEE REIMB`, `TRANSFER EXPRESS PRINTING SETTLEMENT`, sumando `-$23,330.00`).
- **Fallo Legacy:** Al ver `"TRANSFER"` en esos 10 clientes B2B, el motor Legacy los clasificó como `Internal transfer`, restándole **`$7,776.67/mes`** de ventas reales al negocio (`$80,228.21` vs `$88,004.88`) y recortando su oferta en `-$9,332.00` (`$96,273.86` vs `$105,605.86`).
- **Corrección Reviewer (10 flags):** Rescató los 10 cobros con `"TRANSFER"` devolviéndolos a ingresos (`+$9,332.00` en oferta) y, gracias al guardrail de supresión de cambios benignos, mantuvo **0 falsas alarmas** sobre las otras 25 facturas B2B con `"CAPITAL"` y `"ADVANCE"`.

### `BIZ_09` — Turnaround Fabrication Inc (Manufactura — Embargos, Quiebra y Deuda)
- **Problema:** El negocio tiene embargos del IRS (`IRS USATAXPYMT LEVY 941`), tasas de quiebra (`US BANKRUPTCY TRUSTEE CHAPTER 11 FEE`), liquidación en casa de empeño (`EZ PAWN`) y un pago mensual de `+$1,200.00` a `FREEDOM DEBT RELIEF MONTHLY SETTLEMENT`.
- **Fallo Legacy:** Por la palabra `"SETTLEMENT"`, clasificó el pago a `FREEDOM DEBT RELIEF` como `Revenue verification` en vez de `High risk — debt settlement payments`, subestimando `high_risk_share_of_debits` (`0.1223` vs `0.1401`).
- **Corrección Reviewer (1 flag):** Reclasificó el débito de `$1,200` a `High risk — debt settlement payments`, corrigiendo el indicador de estrés financiero a **`14.01%`** de los débitos totales.

### `BIZ_10` — GreenScape Seasonal Landscaping (Paisajismo — Ventana Truncada de 61 Días)
- **Problema:** El comercio entregó solo **61 días** de historia bancaria (`2026-01-01` a `2026-03-02`, con `-$38,316.28` en ventas totales).
- **Fallo Legacy:** Aunque clasificó bien las transacciones (`Flags = 0`), el motor de métricas Legacy dividió `-$38,316.28` entre `3.0` meses (asumiendo 90 días), calculando un ingreso mensual de `$12,772.09` en lugar del ritmo real de **`$18,844.07/mes`** (`$38,316.28 / (61 / 30.0)`). Eso causó un error de **`$6,071.98/mes`** (**32.2% de recorte injusto**) y redujo la oferta de `$22,612.89` a **`$15,326.51`**.
- **Corrección Reviewer:** Detectó `window_coverage_days = 61 < 85` (`is_truncated_window = True`) y normalizó el cálculo mensual por los 61 días reales, restableciendo la oferta de **`$22,612.89`**.
