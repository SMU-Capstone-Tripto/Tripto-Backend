from fastapi import APIRouter
from app.api.v1.endpoints import auth, friends, travel_feed, travel, schedule, memo, share, chat, agent, vote

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(friends.router)
api_router.include_router(travel.router)
api_router.include_router(schedule.router)
api_router.include_router(travel_feed.router)
api_router.include_router(memo.router)
api_router.include_router(share.router)
api_router.include_router(chat.router)
api_router.include_router(agent.router)
api_router.include_router(vote.router)

