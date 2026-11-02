# 部署手册（CPU / GPU 分离）

> 本项目所有 LLM / Embedding / Rerank 都走在线 API。CPU 机器承载应用；
> GPU 机器默认只验证驱动，不启动任何模型。完整背景见 `../Docs/开发手册.md` 第 11 章。

## 1. 文件

```text
deploy/
├── compose/
│   ├── docker-compose.dev.yml   # 本地：mysql redis qdrant migrate api worker（热更新）
│   ├── docker-compose.cpu.yml   # 生产：mysql redis qdrant init api worker nginx
│   └── docker-compose.gpu.yml   # 可选叠加：GPU 探针 / 未来 vLLM（profile 门控）
├── nginx/nginx.conf             # 唯一公网入口、SSE、安全头、/metrics 限制
└── prometheus/prometheus.yml    # 可选监控
```

## 2. 服务器前置

- Linux + Docker Engine 24+ 与 compose plugin；建议 2C4G 起。
- 安全组只放行 22 与 `HTTP_PORT`（默认 8080）；不要暴露 3306 / 6379 / 6333。
- 把 `.env` 放到项目根目录（**不要提交到 Git**），至少填：
  `APP_SECRET_KEY`（≥32 字符）、3 个在线 API Key、`ADMIN_PASSWORD`、
  `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`。生产把 `APP_ENV=prod`。
- 语料放到宿主机目录（默认仓库内 `data/books/`），compose 以只读方式挂载。

## 3. 首次部署

```bash
# 本机（已配置 SSH 免密）
DEPLOY_HOST=1.2.3.4 DEPLOY_USER=ubuntu DEPLOY_DIR=/opt/bookmate \
  ./scripts/deploy_remote.sh

# 或在服务器上
cd /opt/bookmate && ./scripts/deploy.sh
```

`deploy.sh` 会：

1. 构建并打标签 `prod-YYYYmmdd-HHMMSS`；
2. 启动 `init`：`alembic upgrade head` + 自举（管理员/种子书/语料入库/检索自检）；
3. 启动 `api` / `worker` / `nginx`；
4. 等 `/healthz`，再在 api 容器里跑 `scripts/smoke_test.py --base-url http://nginx`；
5. 成功后写 `.image_tag`，上一版写 `.image_tag.prev`。

## 4. 日常操作

```bash
# 查看状态与日志
docker compose --env-file .env -f deploy/compose/docker-compose.cpu.yml ps
docker compose --env-file .env -f deploy/compose/docker-compose.cpu.yml logs -f api worker

# 新增书籍：把文件放进 $CORPUS_HOST_DIR，worker 自动入库
docker compose ... exec -T api python scripts/ingest_cli.py --scan   # 或手动触发

# 备份（建议 cron 每日 03:00；默认保留 7 天）
./scripts/backup.sh

# 回滚镜像
./scripts/rollback.sh                    # 回到 .image_tag.prev
./scripts/rollback.sh prod-20261102-1015 # 回到指定标签
```

## 5. GPU 侧（可选）

```bash
docker network create bookmate-net   # 若 CPU 栈尚未创建
docker compose -f deploy/compose/docker-compose.gpu.yml --profile probe up -d
```

当前不启用 vLLM；只有将来决定自建推理时，才取消 `docker-compose.gpu.yml` 中
`vllm` 段的注释，并把 `.env` 的 `LLM_BASE_URL` 指向它。

## 6. 监控（可选）

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.cpu.yml \
  --profile monitoring up -d
```

Prometheus 只绑定 `127.0.0.1:9090`，通过 SSH 隧道访问；`/metrics` 在 Nginx 层
只允许内网网段。

## 7. 恢复

1. 停 `api` / `worker`；
2. 导入 `backups/<stamp>/mysql.sql.gz`；
3. 解包 `qdrant_data.tgz` 到 Qdrant 数据卷；
4. 启动并跑 `scripts/smoke_test.py`。

数据库迁移是向前的；包含破坏性迁移的发布，应依赖备份恢复而不是只回滚镜像。
