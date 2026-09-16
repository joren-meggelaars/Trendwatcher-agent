from typing import Literal

from pydantic import BaseModel


class ScoreRequest(BaseModel):
    source: str
    title: str
    url: str
    raw_content: str


class ScoreResponse(BaseModel):
    item_id: int
    summary: str
    relevance_score: float


class FeedbackRequest(BaseModel):
    item_id: int
    label: Literal["interessant", "niet_interessant"]


class FeedbackResponse(BaseModel):
    status: Literal["ok"] = "ok"
