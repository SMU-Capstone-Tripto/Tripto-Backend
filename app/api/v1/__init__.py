from fastapi import APIRouter
from app.api.v1.endpoints import auth, friends, travel_feed
from app.api.v1.endpoints import travel, schedule, memo, share

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(friends.router)
api_router.include_router(travel.router)
api_router.include_router(schedule.router)
api_router.include_router(travel_feed.router)
api_router.include_router(memo.router)
api_router.include_router(share.router)