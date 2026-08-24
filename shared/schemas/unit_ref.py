"""Typed institutional unit dimensions for ledger aggregation."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UnitType(str, Enum):
    WARD = "ward"
    DEPARTMENT = "department"
    ROTATION = "rotation"
    TRAINING_BATCH = "training_batch"


class UnitRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    unit_id: str
    unit_type: UnitType
    display_name: str
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


class CohortUnitAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    cohort_id: str
    unit_id: str
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
