"""Durable, bounded page work. Compatible with the existing JSON checkpoint column."""

from typing import Literal

from pydantic import Field, model_validator

from app.modules.procurement.analysis_contracts import (
    ClosedModel,
    DocumentReadResult,
    OfferDecisionResult,
    ProviderCallRecord,
)


class PageWork(ClosedModel):
    page: int = Field(ge=1)
    region: int | None = Field(default=None, ge=0, le=3)
    status: Literal["pending", "inflight", "succeeded", "failed", "uncertain"] = "pending"
    result: DocumentReadResult | None = None
    reading_id: str | None = None


class PageAnalysisCheckpoint(ClosedModel):
    kind: Literal["page_analysis_v3"] = "page_analysis_v3"
    business_id: str
    run_id: str
    source_sha256: str
    source_filename: str
    config_sha256: str
    page_count: int = Field(ge=1)
    work: list[PageWork] = Field(default_factory=list)
    duration_seconds: float = Field(default=0, ge=0)
    continuation_round: int = Field(default=0, ge=0, le=2)
    parent_run_id: str | None = None
    previous_calls: list[ProviderCallRecord] = Field(default_factory=list)
    reserved_calls: int | None = Field(default=None, ge=0)
    reader_config: dict = Field(default_factory=dict)
    identity_decisions: dict[str, OfferDecisionResult | None] = Field(default_factory=dict)
    identity_reservations: dict[str, str] = Field(default_factory=dict)
    identity_decisions_halted: bool = False

    @model_validator(mode="after")
    def valid_work_pages(self):
        keys = [(w.page, w.region) for w in self.work]
        if len(keys) != len(set(keys)) or any(w.page > self.page_count for w in self.work):
            raise ValueError("Invalid or duplicate page work")
        if {w.page for w in self.work if w.region is None} != set(range(1, self.page_count + 1)):
            raise ValueError("Missing initial page work")
        return self


class PipelineCheckpoint(ClosedModel):
    kind: Literal["pipeline_pages_v1"] = "pipeline_pages_v1"
    business_id: str
    parameters_sha256: str
    analysis: PageAnalysisCheckpoint
