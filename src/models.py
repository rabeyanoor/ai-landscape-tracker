"""
Pydantic schemas for every entity type this pipeline produces.
Every scraper / LLM-extractor output must validate against one of these
before it is allowed into the final dataset -- this is what keeps
"garbage in" from becoming "garbage in the Google Sheet".
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"
    UNKNOWN = "UNKNOWN"


class Startup(BaseModel):
    name: str
    canonical_name: Optional[str] = None          # filled in by entity resolver
    website: Optional[str] = None
    description: Optional[str] = None
    employee_count: Optional[str] = None            # kept as string: sources give ranges like "11-50"
    founded_year: Optional[int] = None
    location: Optional[str] = None
    industry_tags: list[str] = Field(default_factory=list)
    source: str                                      # e.g. "YCombinator", "Crunchbase", "Dealroom"
    source_url: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class Product(BaseModel):
    name: str
    canonical_name: Optional[str] = None
    startup_name: Optional[str] = None                # links to Startup.canonical_name
    startup_canonical_name: Optional[str] = None
    description: Optional[str] = None
    pricing_model: PricingModel = PricingModel.UNKNOWN
    price_text_raw: Optional[str] = None
    category: Optional[str] = None
    upvotes: Optional[int] = None
    website: Optional[str] = None
    source: str                                        # e.g. "ProductHunt", "There's An AI For That"
    source_url: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class ResearchPaper(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: Optional[str] = None
    arxiv_id: Optional[str] = None
    published_date: Optional[str] = None               # ISO format
    github_url: Optional[str] = None
    github_stars: Optional[int] = None
    paperswithcode_url: Optional[str] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class NewsArticle(BaseModel):
    title: str
    summary: Optional[str] = None
    url: str
    published_at_iso: str                               # normalized ISO-8601, MUST be within freshness window
    published_at_raw: Optional[str] = None               # original text e.g. "2 hours ago"
    source: str                                          # e.g. "TechCrunch AI"
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("published_at_iso")
    @classmethod
    def must_be_iso(cls, v: str) -> str:
        # Raises if not parseable -- fails fast instead of writing bad dates to the sheet
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class JobPosting(BaseModel):
    title: str
    company: str
    company_canonical_name: Optional[str] = None
    location: Optional[str] = None
    remote: Optional[bool] = None
    salary_text: Optional[str] = None
    url: str
    posted_at_iso: str
    posted_at_raw: Optional[str] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("posted_at_iso")
    @classmethod
    def must_be_iso(cls, v: str) -> str:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class EntityMappingLogEntry(BaseModel):
    raw_name: str
    canonical_name: str
    entity_type: str          # "startup" | "company"
    match_method: str         # "exact" | "fuzzy" | "llm"
    confidence: float
