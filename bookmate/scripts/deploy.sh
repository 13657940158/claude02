#!/usr/bin/env bash
# 墨舟 BookMate 生产部署（在目标服务器上执行）
#
#   ./scripts/deploy.sh                 # 构建 + 启动 + 等待健康 + 烟雾测试
#   ./scripts/deploy.sh --rollback-on-fail
#   DEPLOY_IMAGE_TAG=prod-20261102 ./scripts/deploy.sh   # 指定镜像标签
#
# 约定：
# - 只把 Nginx 暴露到 HTTP_PORT（默认 8080）；数据库/API 都在内网；
# - init 容器负责 alembic 迁移与自举，失败会阻断 api/worker 启动；
# - 部署成功后把镜像标签写入 .image_tag，上一个标签写入 .image_tag.prev 供回滚。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose --env-file .env -f deploy/compose/docker-compose.cpu.yml)
ROLLBACK_ON_FAIL=0
for arg in "$@"; do
  case "$arg" in
    --rollback-on-fail) ROLLBACK_ON_FAIL=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

log() { printf '\n[deploy] %s\n' "$*"; }
die() { printf '\n[deploy] 失败：%s\n' "$*" >&2; exit 1; }

[[ -f .env ]] || die "缺少 .env；请先从 .env.example 复制并填写密钥"
# shellcheck disable=SC1091
set -a; . ./.env; set +a

HTTP_PORT="${HTTP_PORT:-8080}"
# 注意：.env 里的 IMAGE_TAG 是开发栈用的，不参与生产标签，避免回滚点被覆盖
export IMAGE_TAG="${DEPLOY_IMAGE_TAG:-prod-$(date +%Y%m%d-%H%M%S)}"
PREV_TAG="$(cat .image_tag 2>/dev/null || true)"

log "镜像标签：$IMAGE_TAG（上一个：${PREV_TAG:-无}）"
log "构建镜像"
"${COMPOSE[@]}" build

log "启动依赖 + init + api + worker + nginx"
"${COMPOSE[@]}" up -d --remove-orphans

# api 重建后可能拿到新的容器 IP；Nginx 只在启动时解析一次 upstream。
# 不重建 Nginx 的话，会出现「api 自己 healthy，但经 Nginx 的健康检查一直失败」。
log "重建 Nginx，刷新 upstream 解析"
"${COMPOSE[@]}" up -d --force-recreate --no-deps nginx

# 健康检查走 Nginx 容器内的 wget，不依赖宿主机是否装了 curl
health_ok() {
  "${COMPOSE[@]}" exec -T nginx wget -q --spider http://127.0.0.1/healthz >/dev/null 2>&1
}

smoke() {
  # 在 api 容器里跑烟雾，目标指向 Nginx（验证 反代→API 整条链路）
  "${COMPOSE[@]}" run --rm --no-deps --entrypoint python api \
    scripts/smoke_test.py --base-url "${SMOKE_BASE_URL:-http://nginx}"
}

log "等待 API 健康（最多 180s）"
healthy=0
for _ in $(seq 1 36); do
  if health_ok; then
    healthy=1
    break
  fi
  sleep 5
done

if [[ "$healthy" != "1" ]]; then
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail=80 init api || true
  if [[ "$ROLLBACK_ON_FAIL" == "1" && -n "$PREV_TAG" ]]; then
    log "健康检查失败，自动回滚到 $PREV_TAG"
    IMAGE_TAG="$PREV_TAG" "${COMPOSE[@]}" up -d --no-deps api worker
    "${COMPOSE[@]}" up -d --force-recreate --no-deps nginx
  fi
  die "API 未在 180s 内就绪"
fi

log "烟雾测试（Nginx → API）"
if ! smoke; then
  if [[ "$ROLLBACK_ON_FAIL" == "1" && -n "$PREV_TAG" ]]; then
    log "烟雾测试失败，自动回滚到 $PREV_TAG"
    IMAGE_TAG="$PREV_TAG" "${COMPOSE[@]}" up -d --no-deps api worker
    "${COMPOSE[@]}" up -d --force-recreate --no-deps nginx
  fi
  die "烟雾测试未通过"
fi

[[ -n "$PREV_TAG" ]] && printf '%s\n' "$PREV_TAG" > .image_tag.prev
printf '%s\n' "$IMAGE_TAG" > .image_tag

log "部署完成：http://127.0.0.1:${HTTP_PORT}"
"${COMPOSE[@]}" ps
