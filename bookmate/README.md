# 墨舟 BookMate

根据你的性格与目标，替你选书、拆书、排计划、陪你读，并记住你读到哪儿、喜欢什么。

## 先看文档

| 文档 | 内容 |
| --- | --- |
| [协作与输入清单](../Docs/协作与输入清单.md) | 分工、已完成事项、当前待提供的部署输入 |
| [需求文档](../Docs/需求文档.md) | 范围、功能、验收标准 |
| [开发手册](../Docs/开发手册.md) | 架构、模块、API、部署、评测 |
| [验收报告](../Docs/验收报告.md) | 验收结果、覆盖率、性能、已知限制 |
| [执行计划](../Plan/1.%20读书智能客服系统_plan.md) | Wave 顺序与验证 |

## 环境变量

`.env` 已生成模板，填写其中 **5 个必填项**：

1. `APP_SECRET_KEY`（生成：`py -3 -c "import secrets;print(secrets.token_hex(32))"`）
2. `LLM_API_KEY`
3. `EMBEDDING_API_KEY`（可与 LLM Key 相同）
4. `RERANK_API_KEY`（可与 LLM Key 相同）
5. `ADMIN_PASSWORD`

## 本地运行

```bash
# 1) 起依赖 + 迁移 + API + 语料 worker（代码挂载，改完自动 reload）
docker compose --env-file .env -f deploy/compose/docker-compose.dev.yml up -d --build

# 2) 打开 http://127.0.0.1:8000
#    管理员：ADMIN_EMAIL / ADMIN_PASSWORD（见 .env）

# 3) 健康与烟雾
curl -s http://127.0.0.1:8000/readyz
python scripts/smoke_test.py --base-url http://127.0.0.1:8000

# 4) 测试（容器内已装 dev 依赖）
docker compose --env-file .env -f deploy/compose/docker-compose.dev.yml exec -T api python -m pytest -q

# 5) 端到端验收（真实在线模型；注册→问卷→推荐→检索问答→导读→打卡→伴读→笔记→历史）
docker compose --env-file .env -f deploy/compose/docker-compose.dev.yml \
  exec -T api python scripts/e2e_acceptance.py --base-url http://127.0.0.1:8000
```

把 `.txt` / `.md` 书稿按 `书名-作者.txt` 放进 `data/books/`，worker 会按
`CORPUS_WATCH_SECONDS` 自动入库并接入检索；管理员也可在 `/admin` → 入库任务
直接上传 TXT / Markdown / 文字版 PDF（PDF 自动转 TXT，扫描版需先 OCR）。也可手动：

```bash
docker compose ... exec api python scripts/ingest_cli.py --scan
```

评测（真实调用在线模型，先 `--limit 3` 验证链路）：

```bash
docker compose ... exec -T api python scripts/run_eval.py --limit 3 --run-name smoke
```

## 生产部署（CPU / GPU 分离）

```bash
# 服务器首次准备 .env 后
./scripts/deploy.sh                 # 构建 + init 迁移自举 + api/worker/nginx + 烟雾
./scripts/rollback.sh               # 回到上一个镜像标签
./scripts/backup.sh                 # MySQL / Redis / Qdrant / 语料
```

- CPU 栈：`deploy/compose/docker-compose.cpu.yml`（唯一公网入口 Nginx，默认 8080）。
- GPU 栈：`deploy/compose/docker-compose.gpu.yml`（profile 门控；**当前不启动任何模型**）。
- 反向代理与安全头：`deploy/nginx/nginx.conf`；监控：`--profile monitoring`。

## 运行边界

- LLM / Embedding / Rerank 全部走**在线 API**，本地与服务器不加载模型。
- 本地 Docker 只承载 MySQL / Redis / Qdrant / 应用 / 队列。
- 首次启动自动完成：建表 → 导入书目 → 语料入库 → 建索引 → 检索自检。
- 腾讯云生产部署在项目验收后执行，脚本与 compose 已就绪。

## 状态（2026-11-02）

- 后端、前端、RAG、评测、部署脚本均已实现；页面路由烟雾 20/20，pytest 35 项通过。
- 端到端验收 28/28：注册→12 题问卷→画像→推荐→书籍/解析→混合检索→RAG 问答与 SSE→
  5 天导读（章节连续且覆盖全书）→打卡→伴读（含注入拦截）→进度→笔记→历史/统计，
  并覆盖 401/404/422 错误路径。
- RAGAS 已跑 2 轮：`ragas_baseline_v1` → `ragas_prompt_v2`，faithfulness 0.6329 → 0.7640；
  基线与 v2 的指标覆盖率不同，比较口径见开发手册 12.5。
- 已知限制：黄金集 19 条（计划目标 50 条）；本地拉不到 `nginx:1.27-alpine`，
  Nginx 运行时配置待腾讯云首部署验证；生产部署需你提供服务器与域名后再执行。
