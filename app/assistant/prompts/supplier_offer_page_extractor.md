Extrae ofertas de una sola pagina de un folleto/lista de proveedor argentino.

Devuelve solamente JSON compatible con el schema solicitado.

Reglas:

- La imagen corresponde a una unica pagina.
- Recorre la pagina completa de arriba hacia abajo y de izquierda a derecha.
- Extrae todos los bloques visibles que tengan producto y precio.
- No te limites a las ofertas destacadas, banners amarillos, promos o productos grandes.
- No inventes productos ni precios.
- Conserva `raw_name` lo mas parecido posible al documento.
- Extrae `brand` solo si aparece o es claramente visible.
- Extrae `unit_size` y `unit` cuando la presentacion aparezca: 900ML, 1KG, 190GRS, 4L, etc.
- Usa `unit` normalizada: `g`, `kg`, `ml`, `l`, `unit`.
- Usa `offer_price` como numero en ARS salvo que el documento indique otra moneda.
- Si el precio parece por bulto, usa `price_type = "pack"` y completa `package_quantity` si aparece. Si no se puede determinar, usa `unknown`.
- Si el precio parece unitario, usa `price_type = "unit"`.
- Completa `page_number` con la pagina indicada.
- Usa `confidence_score` entre 0 y 1.
- Agrega advertencias para productos ambiguos, precios dudosos o bloques que parezcan incompletos.
- Estima en `coverage.estimated_offer_blocks` cuantos bloques de oferta con precio ves en la pagina.
- Completa `coverage.extracted_item_count` con la cantidad de items extraidos.
- Marca `coverage.needs_review = true` si crees que omitiste ofertas o la pagina es demasiado densa.

Ejemplos de interpretacion:

- `CAÑUELAS Aceite Girasol x 900 Ml. $2849`:
  producto con marca `CAÑUELAS`, presentacion `900 ml`, precio unitario.
- `QUESO CREMA x 290 Gr. $2650`:
  producto sin marca clara si no aparece en el documento, presentacion `290 g`.
- Si una pagina tiene 20 precios visibles, la salida deberia tener cerca de 20 items salvo que existan bloques ilegibles.
