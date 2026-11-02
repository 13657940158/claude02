"""认证与账号 schema。"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _validate_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value):
        raise ValueError("邮箱格式不正确")
    if len(value) > 190:
        raise ValueError("邮箱过长")
    return value


class RegisterRequest(BaseModel):
    email: str
    password: str
    nickname: str = Field(default="书友", min_length=1, max_length=32)
    consent: bool = Field(description="是否同意隐私政策与阅读数据用于个性化推荐")

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("密码至少 8 位")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码过长")
        if value.isdigit() or value.isalpha():
            raise ValueError("密码需同时包含字母与数字")
        return value

    @field_validator("nickname")
    @classmethod
    def clean_nickname(cls, value: str) -> str:
        return value.strip() or "书友"


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        return _validate_email(value)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class UserOut(BaseModel):
    id: int
    email: str
    nickname: str
    role: str
    status: str
    avatar_url: str | None = None
    consent_at: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut | None = None


class RegisterOut(BaseModel):
    user_id: int
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class ConsentRequest(BaseModel):
    consent: bool
    version: str = Field(default="v1", max_length=16)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def check_new(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("新密码至少 8 位")
        if value.isdigit() or value.isalpha():
            raise ValueError("新密码需同时包含字母与数字")
        return value
