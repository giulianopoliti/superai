# Plan tecnico - Matching aprendido y precios por pack

## Alcance

Agregar solo dos funciones al flujo actual de procurement:

1. Reutilizar un match aprobado o corregido manualmente.
2. Convertir precios de packs a precio unitario antes de comparar.

No incorporar Jev, embeddings, RAG ni otro motor de matching.

## 1. Reutilizar aprobaciones manuales

La base ya existe en `product_match_feedback`; no crear una tabla nueva.

### Implementacion

1. Al aceptar o corregir un candidato, guardar como ahora:
   - `business_id`;
   - `supplier_offer_item_id`;
   - `candidate_product_id` definitivo;
   - `relationship_type`;
   - usuario y fecha.
2. Antes del fuzzy matching, consultar la ultima decision por:

```txt
business_id + supplier_id + supplier_offer_item.normalized_name
```

3. Si la ultima decision fue aceptada:
   - devolver directamente el producto confirmado;
   - usar `confidence_score = 1`;
   - agregar `confirmed_by_feedback` a `reasons`;
   - no ejecutar fuzzy matching.
4. Si fue rechazada:
   - excluir solamente ese `product_id`;
   - seguir buscando otros candidatos.

Resultado esperado:

```txt
Primera lista:  PLAYADITO X10 -> usuario corrige a PLAYADITO SUAVE X10
Siguiente lista del mismo proveedor: match directo con PLAYADITO SUAVE X10
```

Archivos:

- `app/modules/procurement/review.py`
- `app/modules/procurement/matching.py`
- `app/db/repositories/sql_procurement.py`
- tests de matching y review

Este comportamiento ya esta parcialmente implementado. La tarea principal es asegurar accept,
correct, reject, aislamiento por tenant y proveedor mediante tests regresivos.

## 2. Normalizar precios de packs

### Contrato minimo

Mantener:

- `offer_price`: precio publicado;
- `package_quantity`: unidades del pack;
- `price_type`: `unit`, `pack` o `unknown`.

`ExtractedSupplierOfferItem` ya tiene `price_type`. Hay que propagarlo a `SupplierOfferItem`, al
modelo SQL y al repositorio. Esto requiere una migracion pequena en `supplier_offer_items`.

### Parsear cantidad

Agregar en `app/modules/procurement/normalization.py`:

```python
parse_package_quantity(name: str) -> int | None
```

Casos iniciales:

```txt
COCA COLA ZERO X6 -> 6
COCA COLA ZERO 6U -> 6
JABON 3X125G      -> 3
JABON 125G X3     -> 3
PACK 12           -> 12
```

### Calcular precio comparable

Agregar una funcion deterministica:

```python
normalize_offer_price(
    offer_price: Decimal,
    package_quantity: int | None,
    price_type: str,
) -> Decimal | None
```

Reglas:

```txt
unit    -> offer_price
pack    -> offer_price / package_quantity
unknown -> None y recommendation = review
```

Ejemplo:

```txt
Coca Cola Zero x6, precio pack $12.000
precio comparable = 12.000 / 6 = $2.000
```

### Detectar un posible precio de pack

Si el texto contiene `xN` y `offer_price / current_cost` esta cerca de `N`, inferir que el precio
publicado corresponde al pack, dividirlo por `N` y registrar la razon:

```txt
price_normalized_by_inferred_pack=N
```

Si la base del precio sigue siendo ambigua, no dividir y dejar el caso en revision. Un precio alto sin
cantidad de pack nunca activa la inferencia.

### Aplicar el precio normalizado

Usar el precio comparable para:

- `cost_difference`;
- `cost_difference_percentage`;
- `estimated_margin_percentage`;
- seleccion del mejor proveedor;
- recomendacion `buy`, `review` o `do_not_buy`.

Archivos:

- `app/modules/procurement/normalization.py`
- `app/modules/procurement/schemas.py`
- `app/modules/procurement/supplier_offers.py`
- `app/modules/procurement/matching.py`
- `app/modules/procurement/supplier_price_comparison.py`
- `app/db/models.py`
- `app/db/repositories/sql_procurement.py`
- migracion Alembic para `price_type`
- UI de revision para mostrar precio original y precio unitario

## Orden de implementacion

1. Agregar tests regresivos para accept, correct y reject.
2. Completar la reutilizacion de feedback si algun caso falla.
3. Persistir `price_type`.
4. Implementar `parse_package_quantity`.
5. Implementar `normalize_offer_price`.
6. Usar el precio normalizado en matching y comparacion de proveedores.
7. Mostrar ambos precios en la revision.
8. Ejecutar `ruff` y `pytest`.

## Tests minimos

- Un accept se reutiliza en la siguiente lista.
- Un correct usa el producto corregido.
- Un reject excluye solo ese producto.
- El feedback no cruza negocios ni proveedores.
- `X6`, `6U`, `3X125G`, `125G X3` y `PACK 12` se parsean.
- Un precio unitario no se divide.
- Un precio de pack se divide con `Decimal`.
- Un precio `unknown` ambiguo queda en revision.
- Un `xN` cuyo precio coincide con `current_cost * N` se divide y queda marcado como inferido.
- Un ratio alto sin `xN` no divide el precio.
- La comparacion de proveedores usa el precio unitario comparable.

## Criterios de aceptacion

- Un match aprobado no vuelve a quedar en duda para el mismo negocio, proveedor y nombre
  normalizado.
- `Coca Cola Zero x6` con precio de pack se compara dividiendo por seis.
- Un `x6` compatible con el costo de seis unidades se normaliza y queda marcado como inferido.
- Un `x6` ambiguo queda en revision.
- Ninguna regla se agrega a prompts y ningun calculo depende de Jev.
