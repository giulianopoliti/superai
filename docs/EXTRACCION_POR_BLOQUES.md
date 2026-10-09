# Extracción dual por bloques

El proveedor `auto` divide los PDF en bloques de hasta 6 páginas. Procesa como máximo
2 bloques a la vez, con una lectura de Gemini y otra de OpenAI simultáneas por bloque.
Un PDF de 27 páginas produce 5 bloques y 10 llamadas iniciales de extracción.
Las relecturas individuales comienzan después de finalizar todos los bloques.

## Configuración

Valores predeterminados, también documentados en `.env.example`:

| Variable | Valor | Alcance |
| --- | --- | --- |
| `DOCUMENT_EXTRACTION_CHUNK_SIZE` | `6` | Entre 1 y 6 páginas por bloque |
| `DOCUMENT_EXTRACTION_CHUNK_WORKERS` | `2` | Entre 1 y 2 bloques activos |
| `DOCUMENT_EXTRACTION_RETRY_WORKERS` | `2` | Entre 1 y 2 páginas en relectura |
| `DOCUMENT_EXTRACTION_TIMEOUT_SECONDS` | `60` | Timeout HTTP de los lectores |
| `DOCUMENT_EXTRACTION_RETRY_LOW_COVERAGE` | `true` | Releer cobertura inferior al 70% |
| `JEV_RECONCILIATION_MODEL` | `jev-1.13.0` | Versión fija para evaluación shadow |

El modo dual requiere ambas claves de lectores. Con `JEV_API_KEY` o
`TYPESAFE_AI_API_KEY` configurada se evalúan pares ambiguos en shadow; sin clave,
esos pares igualmente se conservan para revisión. Las decisiones de Jev nunca
autorizan una fusión. No hay selectores nuevos en la interfaz.

El timeout se aplica en los SDK, no cancelando futures: un reintento de transporte
ocupa el mismo slot hasta finalizar. El tiempo total puede superar 60 segundos por
los reintentos del SDK. La concurrencia máxima de lectores por documento es 4.
Los PDF de bloques se envían inline; no quedan archivos subidos a los proveedores.
Los PDF temporales locales se eliminan al salir del contexto, incluso ante errores.

## Páginas, cobertura y relecturas

Ambos lectores devuelven una entrada por página, con ofertas, página local, cobertura
estimada y advertencias. Gemini tiene un camino específico que evita su recorrido
interno por imágenes. El backend traduce la numeración local una sola vez mediante
`start_page + local_page - 1`. Una página inválida o contradictoria queda sin asignar
y requiere revisión; no se adivina el número original.

Se relee una página como máximo una vez cuando no hay ofertas, un lector devuelve
cero, faltan reportes/números de página, falla un lector, hay advertencias de cobertura
o los conflictos alcanzan 3 pares o el 25% de la lectura más extensa de esa página.
La cobertura baja es menos del 70% de los bloques estimados. Desactivar su relectura
no desactiva los demás motivos ni elimina su registro para revisión.

Cada relectura usa los proveedores disponibles. Errores permanentes como
`invalid_json_schema`, clave inválida o modelo inexistente deshabilitan ese lector
por el resto del documento (pueden terminar hasta dos bloques ya iniciados).
Se guardan tipo, código, estado HTTP, parámetro y request ID, sin credenciales ni
cuerpos de solicitud. No se relee una página sana sólo por faltar un lector
deshabilitado; la falta de verificación independiente sigue visible para revisión.
Las ofertas previamente recuperadas se conservan
si el nuevo intento queda vacío o falla. Si aparece evidencia contradictoria, la oferta
anterior permanece marcada para revisión humana. Los intentos originales se conservan
en metadata. El matching no convierte una observación pendiente de revisión en compra.

## Reconciliación y persistencia

La comparación es por página, con emparejamiento uno a uno. Nombres equivalentes,
unidades normalizadas y campos comerciales compatibles permiten resolver en backend.
Se selecciona una observación según su completitud y confianza, se normaliza una copia
y se completan solamente campos faltantes compatibles. Las observaciones originales
de Gemini y OpenAI permanecen intactas, incluyendo sus números locales de página.

Los conflictos de precio, moneda, tipo de precio, presentación o cantidad por bulto
no se fusionan. Los pares no resueltos quedan en `unresolved_offers` dentro de la
metadata del documento, con ambas observaciones. No generan nuevas ofertas comprables.
Una oferta válida anterior a un conflicto en relectura se conserva con
`resolved_by=human_review`. Jev sólo recibe pares ambiguos y su salida validada se
registra bajo `jev_shadow`; `jev_shadow_status` describe su evaluación sin cambiar
el estado operativo del par.

La metadata de cada oferta canónica contiene `agreement_score`, `agreement_status`,
`selected_source`, `resolved_by` y `provider_observations`. El score de lectores es
independiente del `confidence_score` del candidato de catálogo. El catálogo reconoce
equivalencias físicas de unidades, por ejemplo 1 kg y 1000 g.

La metadata del documento incluye:

- Páginas, tamaño/cantidad de bloques, duración y pico medido de solicitudes activas.
- Llamadas iniciales y llamadas de relectura; cuentan invocaciones del lector, no
  intentos HTTP internos del SDK ni preguntas de Jev.
- Páginas releídas y sus motivos; páginas con baja cobertura y revisión pendiente.
- Ofertas por página y por proveedor tras incorporar las relecturas.
- Fallos históricos por proveedor/página, aun si una relectura posterior tuvo éxito.
- Ofertas confirmadas, de un solo lector, conflictos y decisiones shadow exitosas.
- `provider_attempts`, con resultados y fallos de cada bloque/relectura para auditoría.

Se utilizan las columnas JSON existentes, sin migraciones. Un documento sin ofertas
tras agotar las relecturas se guarda como `failed`, con sus métricas y páginas pendientes,
si se proporcionó un proveedor comercial. La interfaz muestra el resumen y la coincidencia
entre lectores en cada oferta disponible para comparación.

## Coincidencias automáticas y revisión de bultos

La aceptación del candidato identifica el producto; no crea pedidos, compras ni
movimientos de stock. Se acepta automáticamente el primer candidato cuando sus
nombres normalizados conservan exactamente los mismos atributos, hay una
presentación explícita, el score es al menos 0,80 y la diferencia con el segundo
es al menos 0,08. Nombres duplicados en catálogo, variantes ausentes, diferencias
de marca/presentación o conflictos de lectura impiden esa aceptación. También se
reutiliza una única asociación humana anterior para ese proveedor y comercio.

La decisión queda marcada con `auto_accepted_identity_v1`, sin crear feedback
humano ficticio. Al comparar nuevamente se reevalúan las aceptaciones automáticas;
se conservan las decisiones manuales. Un candidato aceptado puede seguir teniendo
el precio pendiente de revisión o una recomendación de no comprar.

Después de extraer, las ofertas marcadas pack/unknown o con indicios de bulto pasan
por una consulta adicional a OpenAI con su página original. El backend sólo aplica
respuestas con confianza >=0,90, evidencia y cantidad válida cuando es un pack.
Kimbies x48 puede así quedar como un sobre con 48 toallitas, con precio por sobre y
`package_quantity=null`. La revisión no modifica el precio ni las observaciones
originales. Fallos, falta de página o ambigüedad dejan `price_type=unknown`; no se
divide por una cantidad inferida. Una relación entre precio y costo por sí sola
tampoco permite dividir automáticamente un precio. Las cargas manuales/local_text
siguen usando los datos explícitos de su entrada.

Estas consultas son secuenciales, posteriores a los lectores, sin retries de SDK,
y quedan contadas en `pack_review_count` y `pack_needs_review_count`. Añaden tiempo
al procesamiento. La duración de lectores no incluye esta fase. No hay migraciones.
Los documentos anteriores necesitan una nueva carga para revisar la evidencia del
PDF, que no se conserva tras procesarla. Recomparar aplica las reglas de identidad
a los datos existentes, sin corregir automáticamente extracciones anteriores.

Se corrigió el esquema de números enviado a OpenAI: `Decimal` mantiene su tipo en
el backend y se publica como `number` en JSON Schema, evitando la expresión regular
con lookaround que la API rechazaba. Las presentaciones `1.5 kg` y `1,5 kg` conservan
el decimal; el parser monetario argentino mantiene su tratamiento de miles.
Registros antiguos con presentación contradictoria al nombre se dejan en revisión,
sin modificar masivamente el catálogo.

## Verificación de cambios

```powershell
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m ruff check app tests
node --check app/static/app.js
```

Las pruebas usan lectores simulados y SQLite; no consumen servicios externos.
Cubren concurrencia, barrera entre fases, remapeo, recuperación parcial, normalización,
shadow y persistencia por la API con aislamiento por `business_id`.
La precisión y latencia con folletos reales quedan sujetas a evaluación con los proveedores.

Referencias de los contratos utilizados:
[archivos de OpenAI](https://developers.openai.com/api/docs/guides/file-inputs),
[salidas estructuradas de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs),
[SDK de Gemini](https://googleapis.github.io/python-genai/),
[API de TypeSafe](https://docs.typesafe.ai/api).
