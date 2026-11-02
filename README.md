# 墨舟 BookMate

面向阅读场景的智能客服系统：根据性格与目标推荐书目，提供书籍解析、导读计划、伴读与阅读记忆，
并把听书、教育、文旅、金融四个业务域接入同一套客服编排。

## 目录

| 路径 | 内容 |
| --- | --- |
| [`bookmate/`](bookmate/) | 可运行应用：FastAPI + MySQL + Redis + Qdrant + Nginx |
| [`Docs/需求文档.md`](Docs/需求文档.md) | 范围、功能与验收标准 |
| [`Docs/开发手册.md`](Docs/开发手册.md) | 架构、API、部署、评测与排障 |
| [`Docs/验收报告.md`](Docs/验收报告.md) | 验收结果、性能数据与已知限制 |
| [`Plan/`](Plan/) | 分阶段执行计划 |

## 快速开始

```bash
cd bookmate
cp .env.example .env       # 填写 LLM / Embedding / Rerank Key 与管理员密码
docker compose --env-file .env -f deploy/compose/docker-compose.dev.yml up -d --build
```

打开 <http://127.0.0.1:8000>。生产部署走 `deploy/compose/docker-compose.cpu.yml`，
详见 [`bookmate/README.md`](bookmate/README.md) 与 [`Docs/开发手册.md`](Docs/开发手册.md)。

## 技术栈

- FastAPI、SQLAlchemy 2、Alembic、MySQL 8、Redis 7、Qdrant
- 在线 LLM / Embedding / Rerank API（默认 SiliconFlow）
- 混合检索（向量 + MySQL ngram 全文）、RRF 融合、重排、引用与护栏
- 原生 ESM 前端 + Jinja2，无前端构建步骤
- Docker Compose CPU / GPU 分离部署，Prometheus 指标

## 语料与版权

`bookmate/data/books/` 仅保留公有领域示例（《论语》《道德经》）。请勿上传未授权的
版权书籍；PDF / Markdown / TXT 可通过管理员后台“入库任务 → 导入书籍”上传，
扫描版 PDF 需先 OCR。

## 许可

仓库暂未附带开源许可证。如需公开复用，请先补充 `LICENSE`（例如 MIT）。
