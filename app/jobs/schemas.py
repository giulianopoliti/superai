from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    phase: str
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    status_url: str
    result_url: str


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    next_offset: int | None
