#!/usr/bin/env bash
# 从本机同步到远程服务器并执行部署（腾讯云 CPU 节点）
#
# .env 需要先在服务器上准备好（首次可手工 scp，避免把密钥写进同步历史）：
#   scp .env user@host:/opt/bookmate/.env
#
# 用法：
#   DEPLOY_HOST=1.2.3.4 DEPLOY_USER=ubuntu DEPLOY_DIR=/opt/bookmate ./scripts/deploy_remote.sh
#   DEPLOY_HOST=... ./scripts/deploy_remote.sh --dry-run
#
# 同步内容：代码、模板、迁移、脚本、语料；排除 .env、.git、备份、缓存、评测产物。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# 读取 .env 里的部署项（不打印任何密钥）
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
fi

: "${DEPLOY_HOST:?请在 .env 填写 DEPLOY_HOST 或通过环境变量传入}"
: "${DEPLOY_USER:?请在 .env 填写 DEPLOY_USER 或通过环境变量传入}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/bookmate}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

log() { printf '\n[remote] %s\n' "$*"; }

log "同步 $ROOT → $SSH_TARGET:$DEPLOY_DIR"
RSYNC_ARGS=(-az --delete --human-readable
  --exclude '.env'
  --exclude '.git/'
  --exclude '.venv/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude 'backups/'
  --exclude '.image_tag'
  --exclude '.image_tag.prev'
  --exclude 'eval/reports/'
  --exclude 'eval/adjustments/'
  --exclude 'node_modules/'
)
[[ "$DRY_RUN" == "1" ]] && RSYNC_ARGS+=(--dry-run --itemize-changes)

rsync "${RSYNC_ARGS[@]}" "$ROOT/" "$SSH_TARGET:$DEPLOY_DIR/"

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run 结束，未在远端执行任何命令"
  exit 0
fi

log "远端部署"
ssh "$SSH_TARGET" "cd '$DEPLOY_DIR' && chmod +x scripts/*.sh && ./scripts/deploy.sh"
