# 项目清理与打包 - 设计

> 日期：2026-07-08
> 类型：一次性维护任务
> 目标：清理垃圾文件，保留项目能运行所必需的文件，打包成 7z

---

## 1. 目标

AgentProject 总大小 2.2 GB（其中 backend/.venv 2.0 GB、web/node_modules 89 MB 是运行依赖）。

按用户要求执行清理与打包：
- 保留 RAG 后端、前端、爬虫（包括 Chrome 插件版本）、CLAUDE.md、记忆、爬虫教程
- 删论文、临时文件、旧 CSV
- 输出 7z 格式压缩包

## 2. 方案选择：方案 B（紧凑集）

| 维度 | 选择 |
|------|------|
| 后端 .venv | **保留**（2.0 GB）|
| 前端 node_modules | **保留**（89 MB）|
| 爬虫教程源码 | 保留 |
| Chrome 插件版爬虫 | 保留 |
| 项目源码 | 全部保留 |
| 输出位置 | 项目根目录 |
| 格式 | 7z |

## 3. 删除清单

### 根目录
- `毕设论文_欧阳源.docx`（702 KB）
- `毕设论文_欧阳源_新.docx`（655 KB）
- `毕设模板.docx`（155 KB）
- `~$论文_欧阳源.docx`（临时 Office 锁文件）
- `不澄清，让`（空文件 0 字节）
- `强澄清`（空文件 0 字节）
- `lining_1781782829569.csv`（28 KB 旧版）
- `lining_1782834855811.csv`（273 KB 旧版）
- 保留 `200.csv`（1.1 MB 最新数据）

### 目录级
- `_thesis_build/`（440 KB）—— 论文辅助构建工具，本次不使用
  - 保留 `_thesis_build/fig_png/`（fig 图，可能给论文用）
- 无其他需删除的大目录

## 4. 保留清单（核心）

### 代码
- `backend/` —— FastAPI 后端（含 .venv）
- `web/` —— Vue 3 前端（含 node_modules）
- `rag/` —— RAG 模块、爬虫、教程
- `android/` —— Android 客户端
- `scripts/` —— 启动脚本
- `_thesis_build/fig_png/` —— 论文配图
- `web/src/`, `backend/app/`, `rag/` 全部子目录

### 文档与记忆
- `CLAUDE.md`、`README.md`、`AGENTS.md`、`GIT_GUIDE.md`
- `长期目标.md`、`todo.md`、`start.md`、`更改.md`
- `l论文辅助.md`、`报告.md`、`test_cases_10.md`
- `docs/`（含 progress、optimization、teaching、interview、eval、superpowers）
- `memory/`、`.claude/`、`.codex/`、`.agents/`
- `graphify-out/`（已构建的知识图谱，可选）
- `prompts/`（新建的）
- `assets/`、`tutorial/`（如有）

### 配置
- `package.json`、`package-lock.json`
- `.env.example`、`.dockerignore`、`.gitignore`、`.claudeignore`

### 数据
- `rag/data/products.csv`、`rag/data/images/`
- `200.csv`

## 5. 输出

```
AgentProject_v2026-07-08.7z
位置：项目根目录
```

## 6. 风险

- 7z 在 Windows 默认需 7-Zip 安装
- 包含 .venv 后体积大（解压后约 2.2 GB）
- 7z 压缩率：预计 1.6-1.8 GB
