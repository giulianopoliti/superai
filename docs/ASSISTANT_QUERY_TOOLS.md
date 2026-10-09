# Consultas del Assistant Core

El registro del agente contiene ocho tools de solo lectura. Todas reciben el negocio
desde `ToolExecutionContext`; un argumento de otro negocio se rechaza.

| Tool | Ejemplo de chat |
| --- | --- |
| `sales.top_products` | Productos más vendidos sin bolsa de pan ni huevos, excluyendo las familias carnicería y verdulería |
| `sales.summary` | Resumen de ventas de los últimos 7 días / cuánto vendí hoy / ticket promedio |
| `sales.product_history` | Cuánto vendí de COCA en los últimos 30 días |
| `sales.compare_periods` | Compará ventas del 22/09 al 28/09 con el 15/09 al 21/09 |
| `catalog.search_products` | Buscame COCA / precio de COCA |
| `inventory.stock_status` | Productos sin stock / stock de COCA |
| `catalog.resolve_families` | Resolver nombres de familias del catálogo |
| `catalog.resolve_products` | Resolver nombres o SKU de productos |

## Semántica

- `families` incluye; `exclude_families` excluye. Se usa la familia vinculada al
  producto, con categoría como respaldo para catálogos sin relación de familia.
- Los filtros se aplican antes de ordenar y limitar el ranking.
- Exclusiones genéricas resuelven todas las presentaciones coincidentes, incluso
  productos inactivos con ventas históricas. “Huevos” abarca las presentaciones
  habituales de huevos comunes, no chocolate ni fideos al huevo. La identificación
  depende del nombre del catálogo; denominaciones desconocidas requieren revisión.
- Filtros no resueltos y productos ambiguos piden aclaración. El ejecutor detiene
  los pasos siguientes si una tool requiere aclaración.
- Sin fechas se consultan los últimos siete días terminando en la última venta
  cargada. Hoy, ayer, esta semana y este mes usan calendario argentino.
- El resumen cuenta tickets distintos desde líneas de ventas, no suma tickets por
  producto. Los totales no están limitados al top 10.
- La comparación permite dos rangos explícitos. Sin segundo rango se compara con
  el período inmediatamente anterior de igual duración. Una base cero devuelve
  porcentaje nulo, no infinito ni un porcentaje inventado.
- El historial contiene días con ventas cargadas, hasta 365 días por consulta; la
  respuesta muestra hasta 31 y devuelve todos los días en los datos estructurados.
- Costos cero generan una advertencia y deshabilitan variaciones de margen en
  comparaciones. Las cantidades no se etiquetan como unidades o kilos sin una
  unidad de venta verificada: la unidad del catálogo puede describir el envase.
- Stock, costos y precios corresponden al catálogo importado. `max_stock=0`
  consulta stock cero o negativo; “stock bajo” necesita un umbral.

## Integración y límites

Las cinco nuevas tools están registradas en `build_tool_registry` y disponibles
para el planificador. Las consultas comunes también funcionan con el router de
respaldo por `/assistant/message`, sin llamadas a proveedores LLM.

El planificador recibe sus schemas y ejemplos en `agent_planner.md`. El ejecutor
usa un plan estático: no vuelve a planificar ni transfiere resultados entre pasos.
Las tools de ventas resuelven sus propios filtros, evitando esa dependencia.
Los errores del planificador y baja confianza registran el motivo del fallback
en metadata de la respuesta y en la conversación persistida.

No se modifican datos de producción, ni se agregan migraciones o dependencias.
Validación: pruebas locales del chat y de las tools con SQLite y proveedores falsos.
Estas pruebas no certifican la interpretación de un modelo externo en producción.
