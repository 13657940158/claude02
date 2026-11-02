"""评测用例补充列：金标类别、章节命中率、引用条数（评测管理页展示）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_cases",
        sa.Column("category", sa.String(32), nullable=False, server_default=""),
    )
    op.add_column("eval_cases", sa.Column("chapter_hit_rate", sa.Integer(), nullable=True))
    op.add_column(
        "eval_cases",
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("eval_cases", "citation_count")
    op.drop_column("eval_cases", "chapter_hit_rate")
    op.drop_column("eval_cases", "category")
