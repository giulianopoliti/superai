"""Validate original family membership independently from auxiliary taxonomy."""

from app.providers.catalog_decisions import CatalogDecisionResponse

CHOICES = {"compatible", "incompatible", "unknown"}
STATUS_LABELS = {
    "compatible": "Compatible",
    "incompatible": "Posible familia original incorrecta",
    "unknown": "Datos insuficientes",
    "needs_review": "Decisión incierta: revisar",
}
ANIMAL_FOOD_DEFINITION = (
    "Alimentos para animales o mascotas, secos o húmedos; por ejemplo alimento para "
    "perros y gatos. Excluye alimentos para personas, limpieza, medicamentos humanos "
    "y accesorios para mascotas."
)


def family_question():
    return {
        "type": "choice",
        "name": "original_family_membership",
        "instructions": "¿El producto pertenece a la familia definida?",
        "choices": [
            {"value": "compatible", "description": "Pertenece a esta familia."},
            {"value": "incompatible", "description": "Corresponde a otra naturaleza de producto."},
            {"value": "unknown", "description": "No hay datos suficientes para saberlo."},
        ],
    }


def validate_family_answer(response: CatalogDecisionResponse, *, threshold: float = 0.90):
    result = {"status": "needs_review", "probability": None, "probabilities": {}}
    if not 0.5 < threshold <= 1:
        raise ValueError("Invalid family threshold")
    if len(response.answers) != 1:
        return result
    answer = response.answers[0]
    if answer.type != "choice" or answer.name != "original_family_membership":
        return result
    probabilities = {p.value: p.probability for p in answer.probabilities}
    if (
        len(answer.probabilities) != 3
        or set(probabilities) != CHOICES
        or abs(sum(probabilities.values()) - 1) > 0.01
    ):
        return result
    result["probabilities"] = probabilities
    if answer.choice not in CHOICES:
        return result
    chosen = probabilities[answer.choice]
    others = [p for key, p in probabilities.items() if key != answer.choice]
    result["probability"] = chosen
    # confidence is derived from the same distribution, not independent evidence.
    if chosen < max(others):
        return result
    if answer.choice == "unknown":
        result["status"] = "unknown"
    elif chosen >= threshold and chosen - max(others) >= 0.15:
        result["status"] = answer.choice
    return result
