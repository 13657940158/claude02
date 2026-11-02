"""API v1 聚合路由。"""
from fastapi import APIRouter

from app.api.v1 import admin, auth, books, chat, eval_api, profile, rag, reading

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(books.router)

api_router.include_router(rag.router)

api_router.include_router(profile.router)

api_router.include_router(chat.router)

api_router.include_router(reading.router)
api_router.include_router(eval_api.router)
api_router.include_router(admin.router)
