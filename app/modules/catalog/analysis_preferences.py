from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ProductAnalysisPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_id: str = Field(min_length=1, max_length=64)
    exclude_from_sales_rankings: StrictBool


class ProductAnalysisPreference(BaseModel):
    business_id: str
    product_id: str
    exclude_from_sales_rankings: bool
    updated_at: datetime


class ProductAnalysisPreferenceList(BaseModel):
    business_id: str
    excluded_product_ids: list[str]


class ProductPreferenceNotFound(ValueError):
    pass
