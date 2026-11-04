# 01 — Glosario de Dominio, Signos de Plaid y Fórmula de Crédito

Este documento explica los términos financieros y las reglas matemáticas implementadas en [`src/schemas.py`](../src/schemas.py) y [`src/credit_impact.py`](../src/credit_impact.py).

---

## 1. Glosario de Términos de Suscripción (*Underwriting*)

- **MCA (*Merchant Cash Advance* — Adelanto de Efectivo Comercial):** Financiamiento de corto plazo donde el prestamista adelanta capital y cobra descontando un monto fijo diario o semanal por ACH de la cuenta bancaria del comercio. En Fundo se etiqueta como `Active advance`.
- **Debt Stacking (Apilamiento de Deuda):** Situación en la que un comercio toma múltiples adelantos MCA simultáneos (ej. OnDeck, Credibly, Libertas, Square Capital). Ignorar un MCA activo sobreestima la capacidad de pago del cliente y aumenta la probabilidad de impago.
- **NSF (*Non-Sufficient Funds* — Fondos Insuficientes):** Intento de cobro o cheque rechazado porque la cuenta no tiene saldo suficiente. Los bancos tradicionales cobran una comisión (típicamente `$35.00`) con el concepto `NSF FEE` o `NSF/RET FEE`.
- **Códigos NACHA `R01` y `R09`:** Códigos estándar de la red de pagos ACH en EE. UU. para transferencias rechazadas:
  - `R01`: *Insufficient Funds* (fondos insuficientes).
  - `R09`: *Uncollected Funds* (fondos depositados aún no disponibles).
  Los neobancos sin comisiones (Mercury, Relay, Novo) registran el evento de rechazo en el extracto con monto `$0.00` e incluyen `R01` o `R09` en la descripción.
- **UCC (*Uniform Commercial Code Filing*):** Registro legal de garantía sobre los activos del negocio que presenta un prestamista al otorgar un crédito. Un débito por `UCC FILING FEE` indica que el comercio acaba de tomar deuda garantizada.
- **MAE (*Mean Absolute Error* — Error Absoluto Medio):** Medida en dólares de cuánto se desvía una estimación respecto al valor real auditado (*Ground Truth*):
  $$\text{MAE} = \left|\text{Valor Calculado} - \text{Valor Ground Truth}\right|$$
  Un MAE de `$0.00` indica coincidencia exacta con la realidad auditada.

---

## 2. Convención de Signos de Plaid

Plaid representa los movimientos desde la perspectiva del flujo de salida de la cuenta ([`PlaidTransaction`](../src/schemas.py)):

| Signo de `amount` | Tipo de Movimiento | Propiedad en Código | Ejemplos |
| :--- | :--- | :--- | :--- |
| **`amount < 0`** | **Crédito / Ingreso (*Inflow*)** — Dinero que entra a la cuenta | `tx.is_credit == True` | Cobros por ventas (`-$1,200.00`), transferencias recibidas, desembolsos de préstamos recibidos (`-$25,000.00`). |
| **`amount > 0`** | **Débito / Egreso (*Outflow*)** — Dinero que sale de la cuenta | `tx.is_debit == True` | Pago de alquiler (`+$3,500.00`), nómina, cuotas diarias de MCA (`+$185.00`), comisiones por NSF (`+$35.00`). |
| **`amount == 0`** | **Evento informativo sin cargo** | `tx.is_credit == False` | Notificación de rebote ACH en neobancos (`ACH RETURN R01 VENDOR PYMT BOUNCE`, `$0.00`). |

---

## 3. Los 13 Grupos de Clasificación (`FundoGroup`) y la Invariante de Ingresos

Cada transacción recibe tres atributos de clasificación:
1. `group`: Una de las 13 categorías de [`FundoGroup`](../src/schemas.py).
2. `is_business`: `True` si corresponde a la operación del negocio; `False` si es un gasto o transferencia personal del dueño.
3. `is_revenue`: `True` si cuenta como ingreso operativo mensual para calcular el préstamo; `False` en caso contrario.

### Regla Determinista de Ingresos ([`compute_is_revenue`](../src/schemas.py))
Una transacción **solo** es ingreso operativo (`is_revenue = True`) si cumple **las tres condiciones simultáneamente**:
1. Es una entrada de dinero (`amount < 0`).
2. Pertenece al negocio (`is_business == True`).
3. Su categoría `group` **no** pertenece a los grupos excluidos de ingresos (`EXCLUDED_REVENUE_CREDIT_GROUPS`).

| Categoría (`FundoGroup`) | ¿Puede ser `is_revenue = True` (si `amount < 0` y `is_business = True`)? | Propósito en el Modelo de Riesgo |
| :--- | :---: | :--- |
| `Revenue verification` | **Sí** | Liquidaciones verificadas de procesadores de pago (Square, Shopify, Toast, Clover, seguros médicos, fletes). |
| `None` | **Sí** | En créditos (`< 0`): cobros directos de facturas B2B de clientes. En débitos (`> 0`): gastos operativos normales (nómina, alquiler, insumos). |
| `Not average monthly revenue` | **No** (Excluido) | Dinero que entra a la cuenta pero **no** es venta (ej. desembolso de un préstamo recibido de `$25,000`). |
| `Internal transfer` | **No** (Excluido) | Transferencias entre cuentas propias o aportes del bolsillo del dueño (Zelle personal). |
| `Auto deposit` | **No** (Excluido) | Depósitos automatizados no verificados como ventas operativas. |
| `Active advance` | **No** (Excluido) | Pagos diarios o semanales a otros prestamistas MCA (se usan para calcular `other_funders_daily_payment`). |
| `NSFs` | **No** (Excluido) | Comisiones por fondos insuficientes o rebotes ACH `R01`/`R09` (alimentan `nsf_overdraft_count`). |
| `Overdraft` | **No** (Excluido) | Comisiones por sobregiro bancario (alimentan `nsf_overdraft_count`). |
| `UCC` | **No** (Excluido) | Tasas de registro de garantías UCC. |
| `High risk — gambling` | **No** (Excluido) | Apuestas y casinos (DraftKings, BetMGM). |
| `High risk — bankruptcy` | **No** (Excluido) | Pagos a síndicos o juzgados de quiebra (Capítulo 11 / Capítulo 7). |
| `High risk — debt settlement payments` | **No** (Excluido) | Pagos a empresas de renegociación de deuda (Freedom Debt Relief). |
| `High risk — garnishment` | **No** (Excluido) | Embargos judiciales o gravámenes fiscales del IRS. |
| `High risk — other` | **No** (Excluido) | Casas de empeño (`EZ PAWN`), prestamistas de día de pago (*payday loans*). |

---

## 4. Fórmula de Cálculo de la Oferta de Crédito

Implementada en [`calculate_business_metrics`](../src/credit_impact.py):

### Paso 1: Cálculo de Métricas Base (Ventana Estándar de 90 Días)
- **Ingreso Mensual Promedio (`avg_monthly_revenue`):**
  - En el motor viejo (`state == "legacy"`): divide siempre entre `3.0` meses, asumiendo que el extracto dura 90 días:
    $$\text{avg\_monthly\_revenue}_{\text{legacy}} = \frac{\sum \left|\text{amount}_{\text{is\_revenue}}\right|}{3.0}$$
  - En el motor revisado (`state == "reviewed"`): normaliza por los días reales de cobertura del extracto (`coverage_days`):
    $$\text{avg\_monthly\_revenue}_{\text{reviewed}} = \frac{\sum \left|\text{amount}_{\text{is\_revenue}}\right|}{\text{coverage\_days} / 30.0}$$
- **Pago Diario a Otros Prestamistas (`other_funders_daily_payment`):**
  Suma todos los débitos (`amount > 0`) clasificados como `Active advance` y los divide entre los días hábiles bancarios del período (`65.0` días hábiles en una ventana de 90 días):
  $$\text{other\_funders\_daily\_payment} = \frac{\sum \text{amount}_{\text{Active advance}}}{65.0 \times (\text{coverage\_days} / 90.0)}$$
- **Proporción de Débitos de Alto Riesgo (`high_risk_share_of_debits`):**
  $$\text{high\_risk\_share\_of\_debits} = \frac{\sum \text{amount}_{\text{High risk — *}}}{\sum \text{amount}_{\text{debits}}}$$

### Paso 2: Regla del Acantilado NSF (*NSF Cliff*) y Oferta Final
1. **Si `nsf_overdraft_count > 5`:**
   $$\text{calculated\_offer} = \$0.00 \quad (\text{Rechazo automático por política de riesgo})$$
2. **Si `nsf_overdraft_count <= 5`:**
   $$\text{calculated\_offer} = \max\left(0.0,\; 1.2 \times \text{avg\_monthly\_revenue} - 20.0 \times \text{other\_funders\_daily\_payment}\right)$$

### Sensibilidad Unitaria de la Fórmula
- **Error de `$1,000` en Ingresos (en 90 días):** Mueve el ingreso mensual en `$1,000 / 3 = $333.33`, lo que altera la oferta en `$333.33 * 1.2 = $400.00` (**`$0.40` por cada `$1` de error**).
- **Error de `$1,000` en `Active advance` (en 90 días):** Mueve el pago diario en `$1,000 / 65 = $15.38/día`, lo que altera la oferta en `$15.38 * 20 = $307.69` (**`$0.308` por cada `$1` de error**).
- **Error de `1` evento en `NSFs` (al pasar de 5 a 6 NSFs):** Provoca una caída en escalón del **100% de la oferta** (ej. de `$69,964.29` a `$0.00` en `BIZ_04`).
