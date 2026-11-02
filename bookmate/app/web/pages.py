"""服务端页面路由：模板渲染 + 前端所需的少量上下文。

约定：
- 页面只负责渲染骨架，数据通过 /api/v1 由前端脚本获取；
- 受保护页面不做服务端鉴权（登录态在 localStorage），未登录时由前端跳转登录；
- 页面 URL 统一在这里登记，便于 base.html 导航高亮与 404 处理。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)


def _render(request: Request, name: str, title: str, **context: Any) -> HTMLResponse:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"page_title": title, **context},
    )


PAGES = [
    ("/", "index.html", "墨舟 BookMate · 按你的性格与目标选书"),
    ("/login", "login.html", "登录"),
    ("/register", "register.html", "注册"),
    ("/account", "account.html", "我的账号"),
    ("/privacy", "privacy.html", "隐私说明"),
    ("/onboarding", "onboarding.html", "性格与目标问卷"),
    ("/books", "books.html", "书架"),
    ("/books/{book_id}", "book_detail.html", "书籍详情"),
    ("/recommend", "recommend.html", "为我推荐"),
    ("/chat", "chat.html", "阅读客服"),
    ("/plans", "plans.html", "我的导读计划"),
    ("/plans/new", "plan_new.html", "创建导读计划"),
    ("/plans/{plan_id}", "plan_detail.html", "导读计划详情"),
    ("/companion/{plan_id}", "companion.html", "伴读"),
    ("/progress", "progress.html", "我的阅读"),
    ("/notes", "notes.html", "我的笔记"),
    ("/admin", "admin.html", "管理后台"),
    ("/admin/eval", "admin_eval.html", "评测中心"),
]


def _handler_factory(template: str, title: str):
    async def handler(request: Request) -> HTMLResponse:
        return _render(request, template, title)

    handler.__name__ = f"page_{template.replace('.', '_').replace('/', '_')}"
    return handler


def register_pages(app: Any) -> None:
    for path, template, title in PAGES:
        app.add_api_route(
            path,
            _handler_factory(template, title),
            methods=["GET"],
            response_class=HTMLResponse,
            include_in_schema=False,
        )
