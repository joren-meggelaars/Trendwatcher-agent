from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.classification import ItemCategory

SourceStatus = Literal["kandidaat", "actief", "gedeactiveerd"]
# What a source is for (not to be confused with an *item's* markt/nieuws
# category). vuln_advisory is secondary to the market focus, but the Source
# model has no weight/priority field, so that is not encoded beyond the label.
SourceCategory = Literal[
    "market_ma",
    "market_analysis",
    "vendor_product",
    "microsoft_product",
    "standards_protocols",
    "threat_research",
    "news",
    "vuln_advisory",
]
DiscoveryMethod = Literal["seed", "link_following", "market_sweep", "manual", "mailbox"]
FeedbackLabel = Literal["interessant", "niet_interessant"]


class ScoreRequest(BaseModel):
    source: str
    title: str
    url: str
    raw_content: str
    source_id: int | None = None


class ScoreResponse(BaseModel):
    item_id: int
    summary: str
    relevance_score: float
    category: ItemCategory


class FeedbackRequest(BaseModel):
    item_id: int
    label: FeedbackLabel


class FeedbackResponse(BaseModel):
    status: Literal["ok"] = "ok"


class RecentFeedbackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    summary: str | None


class RuntimeOverridesResponse(BaseModel):
    """GUI overrides of the scheduler's settings, typed (int/float/bool/str)."""

    overrides: dict[str, int | float | bool | str]


class RuntimeEnvReport(BaseModel):
    """The .env values the scheduler is running with (as text), so the admin
    GUI can show what is really in effect. Unknown keys are ignored."""

    values: dict[str, str] = Field(max_length=100)


class RuntimeEnvReportResponse(BaseModel):
    stored: int


class MarkDigestedRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=200)


class MarkDigestedResponse(BaseModel):
    marked: int


class TopItem(BaseModel):
    item_id: int
    title: str
    url: str
    summary: str
    relevance_score: float
    source_url: str


class SourceCreate(BaseModel):
    url: str
    type: str
    discovery_method: DiscoveryMethod = "manual"
    category: SourceCategory | None = None


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    type: str
    status: SourceStatus
    discovery_method: DiscoveryMethod
    category: SourceCategory | None
    notes: str | None
    running_avg_score: float | None
    created_at: datetime


class SourceStatusChange(BaseModel):
    source_id: int
    url: str
    old_status: SourceStatus
    new_status: SourceStatus


class DigestSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    digest_hour: int
    digest_top_n: int
    updated_at: datetime


class DigestSettingsUpdate(BaseModel):
    digest_hour: int = Field(ge=0, le=23)
    digest_top_n: int = Field(ge=1, le=50)
