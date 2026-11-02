"""初始 schema：28 张表 + chunks 全文索引（ngram）。

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS_COLS = (
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)


def upgrade() -> None:
    # ---------------- 账号与画像 ----------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(190), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("nickname", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("consent_at", sa.DateTime(), nullable=True),
        sa.Column("consent_version", sa.String(16), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("login_count", sa.Integer(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uk_users_email"),
    )
    op.create_index("idx_users_role_status", "users", ["role", "status"])

    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("dims", mysql.JSON(), nullable=False),
        sa.Column("goal", sa.String(32), nullable=False),
        sa.Column("interests", mysql.JSON(), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("daily_minutes", sa.Integer(), nullable=False),
        sa.Column("preferred_formats", mysql.JSON(), nullable=False),
        sa.Column("avoid_topics", mysql.JSON(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "profile_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dims", mysql.JSON(), nullable=False),
        sa.Column("goal", sa.String(32), nullable=False),
        sa.Column("interests", mysql.JSON(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pv_user_version", "profile_versions", ["user_id", "version"])

    # ---------------- 书目 ----------------
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("tags", mysql.JSON(), nullable=False),
        sa.Column("intro", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("total_minutes", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("chapter_count", sa.Integer(), nullable=False),
        sa.Column("cover_url", sa.String(512), nullable=True),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("publish_year", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title", "author", name="uk_books_title_author"),
    )
    op.create_index("idx_books_category_status", "books", ["category", "status"])
    op.create_index("idx_books_difficulty", "books", ["difficulty"])

    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("chapter_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_file", sa.String(512), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "chapter_no", name="uk_chapters_book_no"),
    )
    op.create_index("idx_chapters_book", "chapters", ["book_id"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("chapter_no", sa.Integer(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("content", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("qdrant_point_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_status", sa.String(16), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "chunk_no", name="uk_chunks_book_no"),
    )
    op.create_index("idx_chunks_book", "chunks", ["book_id"])
    op.create_index("idx_chunks_chapter", "chunks", ["chapter_id"])
    # MySQL 没有 ngram 解析器可用时自动忽略（Postgres 等场景不执行此语句）
    op.execute("ALTER TABLE chunks ADD FULLTEXT INDEX ft_chunks_content (content) WITH PARSER ngram")

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("payload", mysql.JSON(), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", name="uk_analyses_book"),
    )
    op.create_index("idx_analyses_status", "analyses", ["status"])

    # ---------------- 推荐 ----------------
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("rule_score", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("factors", mysql.JSON(), nullable=False),
        sa.Column("items", mysql.JSON(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rec_user_created", "recommendations", ["user_id", "created_at"])
    op.create_index("idx_rec_batch", "recommendations", ["batch_id"])

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("source", sa.String(24), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", "action", name="uk_fb_user_book_action"),
    )
    op.create_index("idx_fb_book", "feedbacks", ["book_id"])

    # ---------------- 阅读 ----------------
    op.create_table(
        "reading_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("daily_minutes", sa.Integer(), nullable=False),
        sa.Column("total_minutes", sa.Integer(), nullable=False),
        sa.Column("planned_minutes", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_day", sa.Integer(), nullable=False),
        sa.Column("completed_sessions", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_plan_user_status", "reading_plans", ["user_id", "status"])

    op.create_table(
        "plan_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("day_no", sa.Integer(), nullable=False),
        sa.Column("chapter_from", sa.Integer(), nullable=False),
        sa.Column("chapter_to", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("focus", sa.Text(), nullable=False),
        sa.Column("questions", mysql.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("minutes_spent", sa.Integer(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "day_no", name="uk_ps_plan_day"),
    )

    op.create_table(
        "progress",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("chapter_no", sa.Integer(), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("streak_days", sa.Integer(), nullable=False),
        sa.Column("total_minutes", sa.Integer(), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), nullable=True),
        sa.Column("last_read_date", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uk_progress_user_book"),
    )

    op.create_table(
        "reading_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("review", sa.Text(), nullable=True),
        sa.Column("note_count", sa.Integer(), nullable=False),
        sa.Column("minutes_total", sa.Integer(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rh_user_book", "reading_history", ["user_id", "book_id"])

    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("chapter_no", sa.Integer(), nullable=True),
        sa.Column("companion_session_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", mysql.JSON(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_notes_user_book", "notes", ["user_id", "book_id"])
    op.create_index("idx_notes_session", "notes", ["companion_session_id"])

    # ---------------- 记忆 ----------------
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content", sa.String(512), nullable=False),
        sa.Column("embedding_id", sa.String(36), nullable=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("last_used_at", sa.String(32), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_hash", name="uk_mem_user_hash"),
    )
    op.create_index("idx_mem_user_kind_status", "memories", ["user_id", "kind", "status"])

    # ---------------- 对话 ----------------
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.Column("context_summary", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cs_user_status", "chat_sessions", ["user_id", "status"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", mysql.JSON(), nullable=False),
        sa.Column("tool_names", mysql.JSON(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_msg_session_created", "chat_messages", ["session_id", "created_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("args", mysql.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("result_size", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tc_session", "tool_calls", ["session_id"])
    op.create_index("idx_tc_tool_status", "tool_calls", ["tool", "status"])

    op.create_table(
        "companion_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("day_no", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chapter_limit", sa.Integer(), nullable=False),
        sa.Column("refs", mysql.JSON(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cm_session_created", "companion_messages", ["session_id", "created_at"])

    # ---------------- 评测 ----------------
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_name", sa.String(80), nullable=False),
        sa.Column("dataset", sa.String(255), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("metrics", mysql.JSON(), nullable=False),
        sa.Column("baseline_run_id", sa.Integer(), nullable=True),
        sa.Column("delta", mysql.JSON(), nullable=False),
        sa.Column("report_dir", sa.String(255), nullable=False),
        sa.Column("detail_csv", sa.String(512), nullable=True),
        sa.Column("summary_csv", sa.String(512), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_name", name="uk_eval_run_name"),
    )

    op.create_table(
        "eval_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("case_key", sa.String(80), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("ground_truth", sa.Text(), nullable=False),
        sa.Column("contexts", mysql.JSON(), nullable=False),
        sa.Column("refs", mysql.JSON(), nullable=False),
        sa.Column("faithfulness", sa.Integer(), nullable=True),
        sa.Column("answer_relevancy", sa.Integer(), nullable=True),
        sa.Column("context_precision", sa.Integer(), nullable=True),
        sa.Column("context_recall", sa.Integer(), nullable=True),
        sa.Column("failure_type", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_tokens", sa.Integer(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ec_run", "eval_cases", ["run_id"])

    op.create_table(
        "eval_adjustments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("finding", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("evidence_cases", mysql.JSON(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("applied_note", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ea_run", "eval_adjustments", ["run_id"])

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("eval_score", sa.Integer(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uk_prompt_name_version"),
    )
    op.create_index("idx_prompt_active", "prompt_versions", ["name", "active"])

    # ---------------- 任务/运维 ----------------
    op.create_table(
        "ingest_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.String(512), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("done_chunks", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("report", mysql.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_status_created", "ingest_tasks", ["status", "created_at"])
    op.create_unique_constraint("uk_task_book_idem", "ingest_tasks", ["book_id", "idempotency_key"])
    op.create_index("idx_task_book", "ingest_tasks", ["book_id"])

    op.create_table(
        "domain_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain", sa.String(24), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("query", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_dc_domain_status", "domain_calls", ["domain", "status"])

    op.create_table(
        "bootstrap_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stages", mysql.JSON(), nullable=False),
        sa.Column("books_seeded", sa.Integer(), nullable=False),
        sa.Column("books_ingested", sa.Integer(), nullable=False),
        sa.Column("chunks_created", sa.Integer(), nullable=False),
        sa.Column("self_check", mysql.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_bootstrap_status", "bootstrap_runs", ["status"])

    # ---------------- 治理 ----------------
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("payload", mysql.JSON(), nullable=False),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sec_user_created", "security_events", ["user_id", "created_at"])
    op.create_index("idx_sec_kind_created", "security_events", ["kind", "created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=True),
        sa.Column("before", mysql.JSON(), nullable=True),
        sa.Column("after", mysql.JSON(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *TS_COLS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_actor_created", "audit_logs", ["actor_id", "created_at"])
    op.create_index("idx_audit_object", "audit_logs", ["object_type", "object_id"])


def downgrade() -> None:
    for table in (
        "audit_logs",
        "security_events",
        "bootstrap_runs",
        "domain_calls",
        "ingest_tasks",
        "prompt_versions",
        "eval_adjustments",
        "eval_cases",
        "eval_runs",
        "companion_messages",
        "tool_calls",
        "chat_messages",
        "chat_sessions",
        "memories",
        "notes",
        "reading_history",
        "progress",
        "plan_sessions",
        "reading_plans",
        "feedbacks",
        "recommendations",
        "analyses",
        "chunks",
        "chapters",
        "books",
        "profile_versions",
        "profiles",
        "users",
    ):
        op.drop_table(table)
