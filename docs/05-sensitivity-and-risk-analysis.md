# 05 — Simulación Monte Carlo (4 Modos) y Riesgos de Ingesta

Este documento explica los experimentos de sensibilidad ejecutados en [`src/credit_impact.py`](../src/credit_impact.py) (guardados en [`outputs/sensitivity_report.json`](../outputs/sensitivity_report.json)) y las respuestas técnicas a los dos problemas de estructura de datos de la Parte 2.

---

## 1. ¿Qué mide la Simulación Monte Carlo?

Para demostrar qué tipos de errores de etiquetado dañan más las decisiones de crédito de Fundo, el simulador [`run_sensitivity_simulation`](../src/credit_impact.py) parte de las etiquetas reales (*Ground Truth*) de las 2,520 transacciones y las corrompe artificialmente con tasas de error del **2%**, **5%** y **10%** a lo largo de **50 ensayos (*trials*) independientes por cada tasa y modo** (`seed = 42`).

En cada ensayo se recalcula la oferta de los 10 negocios y se suma el error total de oferta del portafolio:
$$\text{Portfolio Offer Error} = \sum_{i=1}^{10} \left|\text{Oferta Perturbada}_i - \text{Oferta Ground Truth}_i\right|$$

### Los 4 Modos de Perturbación Evaluados:
1. **`random_all_categories`:** Cualquier transacción tiene una probabilidad $p \in \{2\%, 5\%, 10\%\}$ de cambiar a una categoría aleatoria de `FundoGroup`.
2. **`false_revenue_only`:** Solo se perturban los **créditos (`amount < 0`)**, invirtiendo si cuentan o no como ingreso operativo (`is_revenue`: `True` $\leftrightarrow$ `False`).
3. **`false_active_advance_only`:** Solo se perturban los **débitos (`amount > 0`)**, convirtiendo pagos normales en cuotas de competidores MCA (`Active advance`) o viceversa.
4. **`false_nsf_only`:** Solo se perturban los **débitos (`amount > 0`)**, convirtiendo gastos normales en eventos `NSFs` o viceversa.

---

## 2. Resultados Medidos (`outputs/sensitivity_report.json`)

### Tabla Comparativa del Error Medio de Oferta en el Portafolio (10 Negocios)

| Tasa de Error ($p$) | 1. `random_all_categories` | 2. `false_revenue_only` | 3. `false_active_advance_only` | 4. `false_nsf_only` |
| :---: | :---: | :---: | :---: | :---: |
| **2.0%** | `$8,184.22` (0.00 cliffs) | `$10,980.98` (Rev MAE: `$11,051.55`) | `$4,544.05` (0.02 cliffs) | **`$67,315.59`** (1.20 cliffs) |
| **5.0%** | `$24,352.33` (0.08 cliffs) | `$25,272.24` (Rev MAE: `$25,794.79`) | `$14,139.69` (0.10 cliffs) | **`$394,007.46`** (6.72 cliffs) |
| **10.0%** | `$132,841.12` (1.66 cliffs) | `$47,844.99` (Rev MAE: `$48,752.52`) | `$27,601.05` (0.18 cliffs) | **`$465,125.40`** (8.00 cliffs) |

*(Nota: "cliffs" = `mean_nsf_cliff_triggers_per_trial`, cantidad promedio de comercios por ensayo cuya oferta colapsó a `$0.00` o revivió desde `$0.00` por cruzar el umbral `NSF > 5`).*

### Conclusiones Clave de la Simulación:
1. **El costo de los errores es fuertemente asimétrico:**
   Con un mismo **5% de error de etiquetado**, equivocarse en `NSFs` (`$394,007.46`) destruye **15.6 veces más valor de oferta** que equivocarse en ingresos (`$25,272.24`) y **27.8 veces más** que equivocarse en `Active advance` (`$14,139.69`).
2. **Por qué `false_nsf_only` es no lineal (Efecto Acantilado):**
   Un comercio típico tiene ~180 débitos en 90 días. Un 5% de falsos positivos sobre 180 débitos genera ~9 falsos NSFs, lo cual supera inmediatamente el límite `NSF > 5` y anula al 100% ofertas de `$50,000`–`$105,000` en un promedio de **6.72 de los 8 comercios aprobados**.
3. **Por qué `false_active_advance_only` tiene mayor riesgo real que su impacto lineal en la fórmula:**
   En la fórmula matemática, omitir `$1,000` de pagos a otro MCA solo mueve la oferta en `$307.69` (`$20 * $1,000 / 65`). Sin embargo, en el negocio real de préstamos SMB, no detectar que el cliente ya tiene 2 o 3 prestamistas debitando a diario significa entrar subordinado en la cuenta bancaria, donde la pérdida en caso de *default* es del 100% del capital prestado.

---

## 3. Riesgo de Ingesta 1: Neobancos sin Comisiones por NSF (`BIZ_05`)

- **El Problema (*Survivorship Bias*):**
  Bancos digitales para pymes (Mercury, Relay, Novo, Brex) no cobran comisiones de `$35` por fondos insuficientes. Si el pipeline busca únicamente cargos con `amount == 35.0` y texto `"NSF FEE"`, cualquier cliente de un neobanco tendrá `nsf_overdraft_count = 0` sin importar cuántos cheques o débitos haya rebotado (`BIZ_05` recibía `$36,581.04` en el motor Legacy teniendo 8 rebotes reales).
- **Solución en Ingeniería de Datos y Features:**
  1. **Detector de Códigos NACHA (`R01`, `R09`, `R16`):** Incluir reglas deterministas y de LLM para transacciones de monto `$0.00` o contra-asientos que contengan `ACH RETURN R01`, `ACH RET R09` o `BOUNCE`.
  2. **Detección de Reintentos (*Retry Clusters*):** Identificar débitos por el mismo monto exacto y mismo proveedor dentro de una ventana de 2 a 5 días hábiles tras un rechazo.
  3. **Feature Explícito `bank_charges_nsf_fees`:** Inferir por el nombre de la institución bancaria (o declararlo en `BusinessProfile`) si el banco cobra comisiones por NSF. Cuando `bank_charges_nsf_fees == False`, el modelo pondera el saldo mínimo diario y las micro-transferencias de emergencia del dueño.

---

## 4. Riesgo de Ingesta 2: Extractos de 61 Días en un Modelo de 90 Días (`BIZ_10`)

- **El Problema:**
  El modelo de suscripción asume 90 días calendario (3.0 meses y 65 días hábiles bancarios). Cuando un comercio recién constituido o con conexión parcial de Plaid entrega solo **61 días** (`BIZ_10`), dividir sus ingresos acumulados entre `3.0` subestima su facturación mensual en un **32.2%** (`1 - 61/90`), reduciendo injustamente su oferta de `$22,612.89` a `$15,326.51`. Al mismo tiempo, sumar NSFs en 61 días sin anualizar permite que un comercio con 4 NSFs en 2 meses (ritmo de 6 NSFs en 90 días) pase por debajo del umbral `NSF <= 5`.
- **Solución implementada ([`calculate_business_metrics`](../src/credit_impact.py) y [`check_window_coverage`](../src/credit_impact.py)):**
  1. **Cálculo automático de cobertura:**
     $$\text{coverage\_days} = (\max(\text{tx.date}) - \min(\text{tx.date})).\text{days} + 1$$
  2. **Bandera de truncamiento:** Si `coverage_days < 85`, se activa `is_truncated_window = True`.
  3. **Normalización proporcional de flujos:**
     - Meses efectivos: $\text{coverage\_days} / 30.0$
     - Días hábiles efectivos para MCA: $65.0 \times (\text{coverage\_days} / 90.0)$
