from collections import Counter
from types import SimpleNamespace

from scripts.run_catalog_sample import choose_sample


def test_sample_is_reproducible_unique_and_covers_families():
    products = [
        SimpleNamespace(id=f"{family}-{index}", category=family, name=f"Producto {index}")
        for family in ["ALIMENTO PARA ANIMALES", *[f"Family {i}" for i in range(19)]]
        for index in range(30)
    ]
    first = choose_sample(products, size=100)
    second = choose_sample(list(reversed(products)), size=100)
    assert [p.id for p in first] == [p.id for p in second]
    assert len({p.id for p in first}) == 100
    families = Counter(p.category for p in first)
    assert len(families) == 20 and families["ALIMENTO PARA ANIMALES"] == 12
