#!/usr/bin/env bash
# 备份：MySQL 逻辑备份 + Redis RDB + Qdrant 存储目录 + 语料目录
#
#   ./scripts/backup.sh
#   BACKUP_RETENTION_DAYS=14 ./scripts/backup.sh
#
# 产物：backups/<时间戳>/
#   mysql.sql.gz        mysqldump --single-transaction
#   redis.rdb           Redis RDB 快照（先执行 SAVE）
#   qdrant_data.tgz     Qdrant 存储目录打包（含 collection 与 payload）
#   books.tgz           语料目录（不含在 Git 中的书稿）
#
# 恢复到新环境时：先停 api/worker，导入 MySQL、解包 Qdrant 到数据卷，再启动。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --env-file .env -f deploy/compose/docker-compose.cpu.yml)

log() { printf '\n[backup] %s\n' "$*"; }
die() { printf '\n[backup] 失败：%s\n' "$*" >&2; exit 1; }

[[ -f .env ]] || die "缺少 .env"
# shellcheck disable=SC1091
set -a; . ./.env; set +a

STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$ROOT/backups/$STAMP"
RETENTION="${BACKUP_RETENTION_DAYS:-7}"
mkdir -p "$DEST"

log "备份到 $DEST"

log "1/4 MySQL"
"${COMPOSE[@]}" exec -T mysql sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --quick --routines --triggers --databases "$MYSQL_DATABASE"' \
  | gzip -9 > "$DEST/mysql.sql.gz"

log "2/4 Redis"
"${COMPOSE[@]}" exec -T redis redis-cli save >/dev/null
"${COMPOSE[@]}" cp redis:/data/dump.rdb "$DEST/redis.rdb" >/dev/null

log "3/4 Qdrant"
QDRANT_CID="$("${COMPOSE[@]}" ps -q qdrant)"
[[ -n "$QDRANT_CID" ]] || die "qdrant 容器未运行"
docker run --rm \
  --volumes-from "$QDRANT_CID" \
  -v "$DEST":/backup \
  alpine:3.20 \
  sh -c 'tar czf /backup/qdrant_data.tgz -C /qdrant/storage .'

log "4/4 语料"
CORPUS_HOST_DIR="${CORPUS_HOST_DIR:-$ROOT/data/books}"
if [[ -d "$CORPUS_HOST_DIR" ]]; then
  tar czf "$DEST/books.tgz" -C "$(dirname "$CORPUS_HOST_DIR")" "$(basename "$CORPUS_HOST_DIR")"
else
  log "语料目录不存在，跳过：$CORPUS_HOST_DIR"
fi

log "校验产物"
for f in "$DEST"/*; do
  printf '  %-24s %s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"
done

if [[ "$RETENTION" =~ ^[0-9]+$ && "$RETENTION" -gt 0 ]]; then
  log "清理 ${RETENTION} 天前的备份"
  find "$ROOT/backups" -mindepth 1 -maxdepth 1 -type d -mtime +"$RETENTION" -print -exec rm -rf {} + || true
fi

log "完成：$DEST"
