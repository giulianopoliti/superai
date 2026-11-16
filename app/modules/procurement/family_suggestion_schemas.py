"""Contracts for backend-validated family suggestions, independent from AI providers."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FamilyChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    interpretation_id: str
    target_family_id: str | None = None
    target_creation_suggestion_id: str | None = None
    reason: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=500)
    policy_version: str = "family-review-v1"

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.target_family_id) == bool(self.target_creation_suggestion_id):
            raise ValueError("Exactly one family target is required")
        return self


class FamilyCreationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    interpretation_id: str
    proposed_name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    includes: list[str] = Field(min_length=1, max_length=30)
    excludes: list[str] = Field(max_length=30)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=500)
    policy_version: str = "family-review-v1"
