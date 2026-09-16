from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SourceStatus = Literal["kandidaat", "actief", "gedeactiveerd"]
DiscoveryMethod = Literal["seed", "link_following", "market_sweep", "manual"]
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


class FeedbackRequest(BaseModel):
    item_id: int
    label: FeedbackLabel


class FeedbackResponse(BaseModel):
    status: Literal["ok"] = "ok"


class RecentFeedbackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    summary: str | None


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
