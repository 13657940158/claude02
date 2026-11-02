"""FastAPI 应用装配。

Wave 0 范围：配置、日志、异常、请求上下文、健康检查、静态页。
后续 Wave 在 include_router 处逐步挂载业务路由。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.metrics import metrics_endpoint
from app.core.middleware import RequestContextMiddleware
from app.services.bootstrap import schedule_startup_bootstrap
from app.services.rag.vector_store import close_qdrant
from app.utils.redis_client import close_redis
from app.web.pages import register_pages

settings = get_settings()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    settings.validate_production()
    missing = settings.missing_online_keys()
    logger.info(
        "startup",
        extra={"extra_fields": {
            "env": settings.app_env,
            "version": __version__,
            "missing_online_keys": missing,
            "llm_mode": settings.llm_mode,
            "domain_mode": settings.domain_mode,
        }},
    )
    bootstrap_task = schedule_startup_bootstrap()
    app.state.bootstrap_task = bootstrap_task
    yield
    if bootstrap_task and not bootstrap_task.done():
        bootstrap_task.cancel()
    await close_redis()
    await close_qdrant()
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description="书籍推荐 / 解析 / 导读伴读 / 智能客服",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_prod else None,
        redoc_url=None,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)

    app.mount("/static", StaticFiles(directory="web/static"), name="static")
    templates = Jinja2Templates(directory="web/templates")
    templates.env.globals.update(app_name=settings.app_name, version=__version__)
    app.state.templates = templates

    register_pages(app)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> HTMLResponse:
        return HTMLResponse(status_code=204)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return await metrics_endpoint()

    return app


app = create_app()
