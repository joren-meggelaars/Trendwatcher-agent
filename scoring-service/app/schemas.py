from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.classification import ItemCategory

SourceStatus = Literal["kandidaat", "actief", "gedeactiveerd"]
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


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    type: str
    status: SourceStatus
    discovery_method: DiscoveryMethod
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
