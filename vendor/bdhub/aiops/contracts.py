from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    request_key: UUID
    name: str = Field(min_length=1, max_length=120)
    market: Literal["mx"] = "mx"
    source_pool: Literal["full_managed", "clue", "campaign"]
    source_label: str = Field(default="", max_length=200)
    product_category: str = Field(default="", max_length=120)
    creator_category: str = Field(default="", max_length=120)


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    oec_id: str = Field(pattern=r"^[0-9]{1,128}$")
    pid: str = Field(pattern=r"^[0-9]{10,32}$")
    owner: str = Field(min_length=1, max_length=80)
    next_step: str = Field(min_length=1, max_length=600)
    next_check_at: datetime | None = None

    @field_validator("next_check_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("timezone_required")
        return value


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    revision: int = Field(ge=1)
    stage: Literal["in_progress", "waiting_creator", "waiting_business", "needs_operator", "closed"]
    owner: str = Field(min_length=1, max_length=80)
    next_step: str = Field(min_length=1, max_length=600)
    next_check_at: datetime | None = None
    _timezone = field_validator("next_check_at")(CaseInput.timezone_required.__func__)


class OperationsError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
