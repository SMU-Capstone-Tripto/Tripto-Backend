from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class VoteCreateRequest(BaseModel):
    vote_type: str              # 'solo' | 'group'
    room_id: Optional[int] = None  # group일 때만 필수


class SnapshotOut(BaseModel):
    snapshot_id: int
    version_num: int
    plan_title: Optional[str]
    itinerary: List[str]
    estimated_cost: Optional[dict]
    city: Optional[str]
    traveldates: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class VoteCastRequest(BaseModel):
    snapshot_id: int


class VoteResultItem(BaseModel):
    snapshot_id: int
    plan_title: Optional[str]
    vote_count: int


class VoteSessionResponse(BaseModel):
    vote_id: int
    vote_type: str
    status: str
    snapshots: List[SnapshotOut]
    results: List[VoteResultItem]
    my_vote: Optional[int]          # 내가 투표한 snapshot_id, 없으면 None
    winner_snapshot_id: Optional[int]
    expires_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class FinalizeResponse(BaseModel):
    travel_id: int
    message: str
