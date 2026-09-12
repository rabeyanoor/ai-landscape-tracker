"""
Canonical Pydantic schemas for every record type this pipeline produces.

All entities share the same envelope shape (schemaVersion / recordType / source /
content / collectedAt) so downstream consumers (LLM extractor, entity resolver,
Sheets exporter) can handle them polymorphically without knowing the concrete
entity type in advance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


class Source(BaseModel):
    name: str
    url: str


# ---------------------------------------------------------------- Startup --

class StartupData(BaseModel):
    employeeCount: Optional[int] = None


class StartupContent(BaseModel):
    entityName: str
    data: StartupData = Field(default_factory=StartupData)


class StartupEntity(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = Field(default="STARTUP", frozen=True)
    source: Source
    content: StartupContent
    collectedAt: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------- Product --

class ProductContent(BaseModel):
    startupName: str
    pricingModel: PricingModel


class ProductEntity(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = Field(default="PRODUCT", frozen=True)
    source: Source
    content: ProductContent
    collectedAt: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------- Research Paper --

class ResearchPaperContent(BaseModel):
    title: str
    authors: List[str] = Field(default_factory=list)
    paper_url: str
    github_url: Optional[str] = None
    github_stars: Optional[int] = None
    published_date: Optional[datetime] = None


class ResearchPaperEntity(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = Field(default="RESEARCH_PAPER", frozen=True)
    content: ResearchPaperContent
    collectedAt: datetime = Field(default_factory=_utcnow)


# ------------------------------------------------------------------- Job --

class JobContent(BaseModel):
    company: str
    date: datetime
    is_remote: bool
    role_family: str


class JobEntity(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = Field(default="JOB", frozen=True)
    content: JobContent
    collectedAt: datetime = Field(default_factory=_utcnow)


# ------------------------------------------------------------------ News --
# Not specified in the original field list, but required by the 6-tab Sheets
# export (Startups / Products / Research Papers / Jobs / News / Entity Log).
# Mirrors the same source/content/collectedAt envelope for consistency.

class NewsContent(BaseModel):
    headline: str
    url: str
    publishedAt: datetime
    summary: Optional[str] = None


class NewsEntity(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = Field(default="NEWS", frozen=True)
    source: Source
    content: NewsContent
    collectedAt: datetime = Field(default_factory=_utcnow)
