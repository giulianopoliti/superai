from pydantic import BaseModel, Field

from app.modules.procurement.analysis_contracts import AnalysisMethod
from app.modules.procurement.schemas import SupplierOfferExtraction


class AnalysisParameters(BaseModel):
    version: int = Field(default=1, ge=1, le=1)
    business_id: str = Field(min_length=1, max_length=64)
    supplier_name: str | None = Field(default=None, max_length=255)
    extraction_provider: str = "auto"
    max_candidates: int = Field(default=1, ge=1, le=50)
    persist_candidates: bool = True
    analysis_method: AnalysisMethod = AnalysisMethod.OPENAI_STRUCTURED
    identity_probability: float = Field(default=0.85, ge=0, le=1)
    price_probability: float = Field(default=0.85, ge=0, le=1)
    decision_confidence: float = Field(default=0.85, ge=0, le=1)
    decision_margin: float = Field(default=0.10, ge=0, le=1)


class PreparedAnalysis(BaseModel):
    business_id: str
    supplier_name: str
    extraction: SupplierOfferExtraction
    request_id: str
    elapsed_seconds: float
    continuation_checkpoint: dict | None = None
