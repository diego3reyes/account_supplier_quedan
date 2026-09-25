# account_supplier_quedan — Quedan de Proveedores (Odoo 19)

Genera e imprime un **Quedan** desde un pago a proveedor (`account.payment`).
Cada hoja carta contiene el mismo Quedan dos veces: **ORIGINAL** arriba y **COPIA** abajo, separadas por una línea de corte.

## Uso

1. En un pago con *Tipo = Enviar* y *Proveedor* (`payment_type = outbound`, `partner_type = supplier`) aparece el campo **Días para pago** y el botón **Imprimir Quedan**.
2. Puede imprimirse en borrador, en proceso o pagado.
3. La primera impresión asigna el correlativo (`Q-000001`, …) de la empresa del pago y **requiere permiso de escritura** sobre el pago. Las reimpresiones conservan el mismo número y solo requieren permiso de lectura.
4. Un pago cancelado o rechazado **no genera un Quedan nuevo**. Si ya tenía correlativo, se puede reimprimir como histórico y la plantilla predeterminada muestra `*** ANULADO ***` (`quedan.is_canceled`).

## Plantilla

Se edita en **Contabilidad → Configuración → Ajustes → Quedan de Proveedores** y es propia de cada empresa (la empresa activa del selector).
La plantilla representa **un solo Quedan**: el sistema la imprime dos veces. Sintaxis:

```
{{ company.name }}                 variables con rutas de punto
{% if supplier.nit %}…{% else %}…{% endif %}
{% for doc in documents %}{{ doc.number }}{% endfor %}   (loop.index, loop.first, loop.last)
```

Variables: `company.*` (name, nit, nrc, address, street, phone, email, logo), `supplier.*` (name, nit, nrc, address, phone, email), `quedan.*` (number, date, amount, currency, payment_days, payment_date, memo, reference, payment_number, documents_count, is_canceled), `copy.label`, `copy.is_original` y `documents` (number, reference, date, amount, currency).

## Limitaciones conocidas

- **Una sola hoja no está garantizada para cualquier cantidad de documentos.** ORIGINAL, línea de corte y COPIA están dentro de una misma página QWeb; cada copia ocupa al menos 124 mm y no tiene alto fijo ni `overflow:hidden`. Con la plantilla predeterminada el límite seguro es de **aproximadamente 9 documentos** (menos con logo o nombres/direcciones largos). Si una copia excede media hoja, la COPIA pasa completa a la hoja siguiente (ORIGINAL y COPIA quedan en hojas distintas). Si una sola copia excede una hoja entera (unos 45 documentos), se parte entre páginas. Nunca se truncan, ocultan ni eliminan documentos para forzar una página.
- **Secuencias**: cada empresa tiene su propia `ir.sequence` (`Q-`, 6 dígitos, sin huecos), creada al instalar el módulo para las empresas existentes y al crear cada empresa nueva, y guardada en `supplier_quedan_sequence_id`. Si una empresa quedara sin secuencia, se repara al imprimir, adoptando una secuencia existente del mismo código y empresa si la hay.
- **Documentos**: salen de `reconciled_bill_ids` estándar de Odoo, es decir, de las facturas desde las que se registró el pago (`invoice_ids`) y de las facturas conciliadas con el asiento del pago. En un pago manual en borrador sin facturas vinculadas, la lista queda vacía hasta que se concilie.
- **NIT / NRC**: se leen de los campos `nit` y `nrc` de `res.company` / `res.partner` si una localización los define (p. ej. `l10n_sv` propio). Si no existe `nit`, se usa el campo estándar `vat`. Si no existe `nrc`, se imprime vacío.
- **Días para pago / fecha estimada**: son solo informativos. La fecha estimada (`quedan.date + días`, en días calendario) se calcula al imprimir y no se guarda en ningún campo contable.
- **Actualizaciones**: actualizar el módulo no sobrescribe las plantillas ya guardadas. La plantilla predeterminada se asigna solo al instalar el módulo o al crear una empresa nueva. Por eso la banda `*** ANULADO ***` solo aparece en empresas que usan la plantilla predeterminada vigente; en una plantilla personalizada hay que agregar `{% if quedan.is_canceled %}*** ANULADO ***{% endif %}` manualmente o usar *Restaurar plantilla predeterminada*.
