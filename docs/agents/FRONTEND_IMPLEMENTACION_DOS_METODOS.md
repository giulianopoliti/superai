# Implementación frontend: dos métodos de lectura

Fecha: 6 de octubre de 2026.
Estado: frontend implementado y probado con respuestas simuladas. El coordinador
informó integración backend A/B conectada, API verificada y suite global con
528 pruebas aprobadas. Esa verificación no se reejecutó desde este chat.

Se siguieron AGENTS.md, PLAN_DOS_METODOS_EXTRACCION.md,
ORQUESTACION_DOS_METODOS.md, FRONTEND_DOS_METODOS_HANDOFF.md y el contrato
publicado en CONTRATOS_DOS_METODOS.md. Skill aplicada: stock-ai-quality-review.
Se preservaron los cambios locales previos. No se editaron backend, settings,
variables de entorno ni schemas; no se instalaron dependencias ni crearon migraciones.

## Comportamiento entregado

- Selector común A/B independiente de Un/Dos PDF, con etiquetas españolas y ayuda.
  A separa datos con OpenAI; el sistema valida y consulta JEV selectivamente si
  quedan dudas tras las reglas. B lee textos con OpenAI; JEV propone su organización
  y el sistema valida. Las decisiones JEV están asistidas por validación
  determinista: probabilidades no calibradas no autorizan aceptación automática.
- GET analysis-methods valida versión, lista conocida y disponibilidad. B no se
  habilita por existencia de claves en el navegador. Una consulta fallida o inválida
  bloquea ambos formularios y ofrece Reintentar; no existe fallback silencioso.
- Multipart conserva extraction_provider=auto y envía analysis_method explícito.
  Negocio, método, ambos archivos y nombres se capturan antes del primer await;
  ambos PDF y nombres se validan antes de enviar el primero.
- Bloqueo compartido de formularios, inputs, selector, modos y negocio. finally
  restaura estados previos y vuelve a aplicar disponibilidad; una segunda operación
  no puede iniciarse mientras la primera sigue activa.
- Dos POST secuenciales. Si falla el segundo, el enlace apunta al primer documento
  guardado; no se repite el primer POST. Un error de refresh del historial tampoco
  provoca repetir uploads ni convierte el análisis guardado en un fallo de lectura.
- Método ejecutado procede de analysis/metadata.analysis; nunca del selector actual.
  Históricos sin summary muestran Método no registrado. Lectura single_source
  OpenAI ya no dice Coincidencia Gemini/OpenAI; las lecturas duales históricas
  conservan su descripción. Errores nuevos no recomiendan Gemini suspendido.
- Estados Parcial y Guardado / Revisar incluyen bloques, identidad, precio, ofertas
  sin vínculo y páginas pendientes. La comparación usa precios comparables, pero
  muestra conteos por proveedor y bloques pendientes fuera de bestOffersByProduct.
- Panel independiente de pendientes en detalle, sin acciones ficticias; GET de
  detalle persistente por ID y tenant permite abrir documentos fuera del historial.
  Si falla, explica el problema y permite reintentar. Solo conserva evidencia de un
  POST ya recibido en memoria; no inventa resultados de un endpoint ausente.
- Historial separa pendientes de coincidencias y bloques. Formateador común mantiene
  el método real en resumen y subtítulo. Textos, motivos y proveedores se escapan;
  IDs se codifican en enlaces. Evidencia larga mantiene saltos y ajuste de línea.
- Ajustes mínimos de foco y móvil; versionado de assets para renovar caché.

## Archivos modificados exclusivamente por esta entrega

- app/static/index.html
- app/static/app.js
- app/static/styles.css
- tests/frontend/analysis_ui.test.cjs (nuevo)
- tests/frontend/fixture_server.py (nuevo)
- docs/agents/FRONTEND_IMPLEMENTACION_DOS_METODOS.md (nuevo)

## Verificación real ejecutada

- `node --check app/static/app.js`: salida 0, sintaxis válida.
- `node --test tests/frontend/analysis_ui.test.cjs`: 9 pruebas aprobadas, 0 fallos.
  Usa node:test, vm, FormData y File nativos; sin paquetes nuevos ni red.
  Comprueba payload A/B y auto legacy; validación previa de ambos archivos;
  snapshot de tenant/método/nombres/archivos; bloqueo compartido; segundo fallo;
  restauración; consulta de capacidades fallida/B no integrado; parciales single
  y double; pendientes sin ofertas; HTML escapado; detalle persistente/histórico.
  El harness ejercita funciones de producción con DOM mínimo: no reemplaza pruebas
  de navegador ni verifica backend real.
- `git diff --check -- app/static/index.html app/static/app.js app/static/styles.css`:
  salida 0. Git avisa normalización LF/CRLF; no reporta errores de whitespace.
- `python tests/frontend/fixture_server.py`: servidor local en 127.0.0.1:8766,
  exclusivamente GET de fixtures; POST devuelve 405. Cerrado al terminar pruebas.
- Navegador integrado contra ese servidor: selector accesible, B deshabilitado con
  razón visible, mismo selector para formulario doble, Tab desde selector enfoca
  Proveedor. Detalle con método B/Parcial y bloque `<img ...>` visible como texto.
  DOM confirma cero nodos img dentro de pendientes. Viewport 320x800: ancho final
  del documento 320, panel 288, evidencia sin truncamiento. Se corrigió desborde de
  toolbar existente y subtítulo del método fuera del historial; viewport restaurado.
  No se hicieron uploads desde navegador. Otras rutas de la fixture son mínimas;
  no se validó plan de compra ni dashboard con este servidor.

## Dependencias y límites del cierre

El contrato publicado exige capacidades reales de GET analysis-methods; POST con
default A, analysis y pending_blocks; metadata.analysis persistida para summaries;
GET /supplier-offers/{document_id}/analysis con aislamiento business_id. La UI
consume esos campos exactos. La disponibilidad de B debe corresponder a integración
de lector/clasificador y claves, y el backend debe rechazar indisponibilidad antes
de leer. El coordinador informó que la integración API está verificada. Las pruebas
de este chat siguen siendo offline y con fixtures; no constituyen evaluación real
de calidad, costo o latencia de los proveedores.

No se declara A/B integrado en producción, ni ahorro, mejora de precisión o latencia.
No se hicieron llamadas pagas ni escrituras en DB del negocio. No se crearon chats,
subagentes, commits ni mensajes a otros chats. Dependencias nuevas: ninguna.

## Cierre acotado de copy

El 6 de octubre se actualizaron exclusivamente las ayudas A/B en app/static/app.js
y app/static/index.html y este informe. No se cambiaron lógica ni flujos.
Se reejecutaron `node --test tests/frontend/analysis_ui.test.cjs` (9 aprobadas,
0 fallos) y `node --check app/static/app.js` (salida 0).
