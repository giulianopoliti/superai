# catalog_full_verification v3

Interpretá el artículo de forma independiente y elegí las opciones que corresponden.
La propuesta generativa es una candidata, no evidencia adicional ni una respuesta
correcta por defecto. Usá el nombre original como evidencia principal.
La familia del sistema es contexto falible, nunca prueba definitiva.
El vocabulario puede estar incompleto: none significa que falta una opción adecuada;
unknown significa que el producto no aporta datos para resolver ese campo.
Cada pregunta es independiente, pero marca, línea y variante cumplen roles distintos.
Las opciones de subcategoría solo son válidas bajo proposed_category.
Todas las etiquetas ofrecidas son candidatas válidas, tanto las existentes como
las nuevas. No elijas none porque una etiqueta aún no existe en el vocabulario.
Café en saquitos es un subtipo de Café: EN SAQUITOS lo respalda explícitamente;
no debe rechazarse por ser una forma de presentación. Para LECHE 0 LACTOSA,
Sin lactosa es un atributo de composición, no una subcategoría.
No confundas el saquito interior con el envase exterior del producto.
Sin lactosa describe composición; no permite inferir contenido graso o envase.
Un sabor no identifica por sí mismo una categoría. No fuerces una clasificación.
Para original_family elegí una familia del sistema compatible con la naturaleza
del producto. No uses la familia original para justificar una coincidencia incorrecta.
Conservá la familia actual si es razonable para la organización de ese negocio;
solo proponé otra cuando la actual sea claramente incompatible. No cambies por preferencia.
Los números de contenido son por envase; pack y compra mínima requieren texto explícito.
