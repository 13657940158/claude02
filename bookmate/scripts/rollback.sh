#!/usr/bin/env bash
# 回滚到上一个（或指定）镜像标签
#
#   ./scripts/rollback.sh                    # 回到 .image_tag.prev
#   ./scripts/rollback.sh prod-20261102-1015 # 回到指定标签
#
# 说明：
# - 只重启 api / worker，不动 MySQL / Redis / Qdrant 数据卷；
# - 数据库迁移是向前的（alembic upgrade），旧镜像只在 schema 兼容时可回滚；
#   若本次发布包含破坏性迁移，请用备份恢复数据库，而不是只换镜像。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --env-file .env -f deploy/compose/docker-compose.cpu.yml)

log() { printf '\n[rollback] %s\n' "$*"; }
die() { printf '\n[rollback] 失败：%s\n' "$*" >&2; exit 1; }

[[ -f .env ]] || die "缺少 .env"
# shellcheck disable=SC1091
set -a; . ./.env; set +a

TARGET="${1:-$(cat .image_tag.prev 2>/dev/null || true)}"
[[ -n "$TARGET" ]] || die "没有可回滚的标签（.image_tag.prev 为空），请显式传入标签"
CURRENT="$(cat .image_tag 2>/dev/null || true)"

if ! docker image inspect "bookmate-api:${TARGET}" >/dev/null 2>&1; then
  die "本地不存在镜像 bookmate-api:${TARGET}；请确认标签或从镜像仓库拉取"
fi

log "当前：${CURRENT:-未知} → 目标：$TARGET"
export IMAGE_TAG="$TARGET"
"${COMPOSE[@]}" up -d --no-deps api worker
# api 重建可能换 IP；Nginx 必须重建才能解析到新容器
"${COMPOSE[@]}" up -d --force-recreate --no-deps nginx

log "等待 API 健康（最多 120s）"
for _ in $(seq 1 24); do
  if "${COMPOSE[@]}" exec -T nginx wget -q --spider http://127.0.0.1/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

if ! "${COMPOSE[@]}" run --rm --no-deps --entrypoint python api \
  scripts/smoke_test.py --base-url "${SMOKE_BASE_URL:-http://nginx}"; then
  die "回滚后烟雾测试失败，请检查日志：${COMPOSE[*]} logs --tail=100 api"
fi

printf '%s\n' "$TARGET" > .image_tag
[[ -n "$CURRENT" ]] && printf '%s\n' "$CURRENT" > .image_tag.prev

log "已回滚到 $TARGET"
