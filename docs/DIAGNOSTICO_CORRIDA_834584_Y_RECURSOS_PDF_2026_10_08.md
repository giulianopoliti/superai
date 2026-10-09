# Corrida 834584: PDFs recortados con imágenes de todo el documento

Documento/run: `8345844a65d944e093f62f6c2884ee1e`, `116561.pdf`, 24 páginas.
Fuentes: detalle de la API local, log pegado por el usuario e inspección offline
del original en `C:/Users/54116/Downloads/116561.pdf`.

## Resultado

La división por lotes se ejecutó: 12 lotes, las 24 páginas intentadas y ninguna
sin intentar. Solo la página 2 produjo lectura útil, con 11 bloques y 10 ofertas.
El análisis duró 973,61 s (16 min 14 s); la petición completa, 992,093 s.

Hubo 25 llamadas de lectura (24 iniciales y una relectura de página 1):

| Resultado de transporte | Cantidad |
|---|---:|
| Éxito | 1 |
| APITimeoutError / WriteTimeout | 20 |
| APIConnectionError / RemoteProtocolError | 3 |
| APITimeoutError / ReadTimeout, en la relectura | 1 |

Además hubo 10 consultas JEV, lo que completa las 35 llamadas del resumen.
Intentar una llamada no demuestra que el servidor haya recibido el cuerpo
completo. Los WriteTimeout indican fallos durante escritura/subida; los logs
no atribuyen la responsabilidad de red a un componente externo específico.

## Error local comprobado

El original pesa 22.706.664 bytes. Cada PDF recortado a una página pesaba
22.700.103–22.700.104 bytes: prácticamente el archivo completo.

Las páginas comparten un diccionario `/Resources` con las 24 imágenes del
folleto. La página 1 invoca `/I1`, la 2 `/I2`, etc., pero `PdfWriter.add_page`
copiaba las 24 imágenes del diccionario compartido al PDF de cada solicitud.
Reducir la cantidad de páginas no reducía el peso transportado. Limpiar objetos
huérfanos por sí solo tampoco lo corrige: las imágenes siguen referenciadas
desde `/Resources`, aunque la página no las dibuje.

Las 24 lecturas iniciales adjuntaron unos 544,8 MB de PDFs antes de base64;
la relectura agregó otro archivo de 22,7 MB. Base64 aumenta todavía más el
cuerpo HTTP. Esto es una causa local concreta de carga excesiva y concuerda
con los 20 WriteTimeout. No prueba que todos los errores de conexión/lectura
tengan una única causa ni garantiza éxito después de corregir el tamaño.

## Corrección

`app/providers/documents/chunking.py` ahora prepara una copia independiente de
los recursos de la página antes de agregarla al PDF del chunk. Conserva las
imágenes invocadas mediante `Do` y sus dependencias. No modifica el original.
Para páginas con anotaciones o XObjects Form mantiene los recursos completos,
pues sus apariencias pueden depender de recursos heredados.

No se rasteriza, recomprime ni baja la resolución. La API pública usada para
copiar páginas y manejar objetos está documentada en
[pypdf PdfWriter](https://pypdf.readthedocs.io/en/6.19.0/modules/PdfWriter.html).

Verificación con el PDF real, sin red/inferencia:

| Dato | Después de corregir |
|---|---:|
| Tamaño mínimo de un PDF de una página | 722.894 bytes |
| Tamaño máximo | 1.156.640 bytes |
| Suma de los 24 archivos | 22.715.456 bytes |
| Imágenes referenciadas por PDF | 1 |

Se renderizaron y compararon las 24 páginas con PDFium a escala 0,7:
dimensiones y bytes de píxeles idénticos en todas. Las pruebas sintéticas
verifican reducción de peso, imágenes usadas, contenido y orden, original
intacto, chunks de una/dos páginas y preservación conservadora de Forms y
anotaciones. Los tests anteriores solo comprobaban páginas/orden con páginas
en blanco; no detectaban el arrastre de imágenes compartidas.

El servidor debe cargar el código actualizado para aplicar esta corrección.
La corrida guardada no cambia. No se repitió inferencia paga durante el diagnóstico.
