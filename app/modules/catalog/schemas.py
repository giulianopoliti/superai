from pydantic import BaseModel, Field

from app.modules.procurement.schemas import Product


class CategoryResolution(BaseModel):
    requested: str
    category: str | None = None
    confidence: float = 0
    candidates: list[str] = Field(default_factory=list)


class ProductResolution(BaseModel):
    requested: str
    product: Product | None = None
    confidence: float = 0
    candidates: list[Product] = Field(default_factory=list)


class CatalogResolution(BaseModel):
    category: CategoryResolution | None = None
    categories: list[CategoryResolution] = Field(default_factory=list)
    excluded_products: list[ProductResolution] = Field(default_factory=list)
