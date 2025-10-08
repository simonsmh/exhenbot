## exhenbot

把 ExHentai 画廊一键变成 Telegraph 图文页，并把链接推送到 Telegram。核心流程：

- 监听含有 ExHentai 链接的消息或使用命令解析
- 抓取画廊与 MPV 图集信息
- 通过 Catbox 托管原图直链
- 使用 EhTagTranslation 数据库翻译标签
- 在 Telegraph 创建图文页
- 去重落库（PostgreSQL 或内置 SQLite），并把消息发送至 Telegram

### 功能

- **消息监听与命令**：自动匹配 `https://e.hentai.org/g/<gid>/<token>`；支持 `/parse` 命令
- **批处理任务**：通过 `/add_task` 添加任务，`/clear_task` 清除当前会话任务；按间隔自动搜索并推送
- **标签翻译**：本地缓存 EhTagTranslation 数据库，离线可用
- **图床镜像**：Catbox URL 上传，失败自动重试
- **数据库**：Tortoise ORM，支持 PostgreSQL/SQLite，自动建表
- **部署**：Dockerfile 与 docker-compose 一键部署

---

## 快速开始

### 方式一：docker-compose（推荐）

1) 复制并编辑环境变量

```bash
cp stack.env .env
# 或直接编辑 stack.env 并在 compose 中使用
```

至少需要：

- `EXH_COOKIE`：浏览器里登录 ExHentai 后的完整 `Cookie` 头（通常包含 `ipb_member_id`, `ipb_pass_hash`, `igneous` 等）
- `TELEGRAM_BOT_TOKEN`：Telegram BotFather 生成

可选但强烈建议：

- `TELEGRAPH_ACCESS_TOKEN`：Telegraph access token；未提供时运行期会自动创建匿名账户，但重启后不会保留令牌

2) 启动

```bash
docker compose up -d
```

compose 默认使用镜像 `ghcr.io/simonsmh/exhenbot:main`，也可以自行构建（见下）。

### 方式二：本地运行（Python 3.13）

使用 uv（推荐）：

```bash
# 安装依赖
uv sync

# 设置环境变量（示例，PowerShell）
$env:EXH_COOKIE = "your_exhentai_cookie_here"
$env:TELEGRAM_BOT_TOKEN = "your_telegram_bot_token_here"

# 运行
uv run -m exhenbot
```

或使用 pip：

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install -e .
python -m exhenbot
```

### 自行构建 Docker 镜像

```bash
docker build -t exhenbot:local .
docker run --env-file stack.env --rm exhenbot:local
```

---

## 配置项

来自环境变量，`exhenbot.config.load_settings()` 会提供合理默认值。

- **LOCAL_DIR**：本地缓存目录，默认 `.exhenbot`

- **TASK_CHECK**：任务校验键值，默认 `exhenbot:exhenbot`（用于 `/add_task` 鉴权）

- ExHentai
  - **EXH_COOKIE**：必填；浏览器复制完整 `Cookie` 头
  - **EXH_SEMAPHORE_SIZE**：并发度，默认 `4`
  - **EXH_QUERY**：高级搜索语句，默认 `parody:"blue archive$" language:chinese$`
  - **EXH_CATOGORIES**：分类位掩码，默认 `1017`
  - **EXH_STAR**：评分下限，默认 `4`
  - **EXH_QUERY_DEPTH**：搜索翻页深度，默认 `1`

- File Uploader
  - **FILEUPLOADER_SEMAPHORE_SIZE**：并发度，默认 `4`

- Telegraph
  - **TELEGRAPH_AUTHOR_NAME**：默认 `exhenbot`
  - **TELEGRAPH_AUTHOR_URL**：可选
  - **TELEGRAPH_ACCESS_TOKEN**：可选；不填则首次运行会创建匿名账号（不持久）

- 数据库
  - **DATABASE_URL**：`postgres://user:pass@host:5432/db`；不填则使用 `sqlite://./cache.db`

- Telegram
  - **TELEGRAM_BOT_TOKEN**：必填
  - **TELEGRAM_JOB_INTERVAL**：定时任务间隔秒，默认 `600`
  - **TELEGRAM_API_BASE_URL**：默认 `https://api.telegram.org/bot`
  - **TELEGRAM_API_BASE_FILE_URL**：默认 `https://api.telegram.org/file/bot`
  - **TELEGRAM_LOCAL_MODE**：`true/false`，默认 `false`
  - **TELEGRAM_SEMAPHORE_SIZE**：可选；设置后控制并发

- Webhook（可选；配置后启用 webhook，否则使用 polling）
  - **TELEGRAM_DOMAIN**：外网可达前缀，如 `https://example.com/`
  - **TELEGRAM_HOST**：监听地址
  - **TELEGRAM_PORT**：监听端口

> 提示：`docker-compose.yml` 中已包含 PostgreSQL 与服务编排，默认读取 `stack.env`。

---

## 使用方式

- 将 Bot 邀请到群/频道或私聊中，发送包含 ExHentai 画廊链接的消息，或使用命令：

```text
/parse https://e.hentai.org/g/<gid>/<token>
```

添加定时任务（将下述 JSON base64 编码后作为参数）：

```json
{"exhenbot":"exhenbot","search":"parody:\"blue archive$\" language:chinese$","catogories":1017,"star":4,"author_name":"exhenbot","author_url":"","query_depth":1}
```

```text
/add_task <base64_of_above_json>
```

任务会按 `TELEGRAM_JOB_INTERVAL` 周期执行搜索并推送新画廊。

清除当前会话的定时任务：

```text
/clear_task
```

消息内容包括：

- 画廊标题与 Telegraph 页面链接
- 翻译后的标签（按命名空间分组）
- 原始画廊链接

---

## 运行原理（简述）

1. 解析画廊页与 MPV 页，抽取全量图片与下载令牌
2. 调用 `imagedispatch` 获取每页直链
3. 把直链镜像到 Catbox，生成公开可访问链接
4. 加载/更新 EhTagTranslation 数据库，批量翻译标签（本地缓存 SHA + JSON）
5. 创建 Telegraph 图文页并返回 URL
6. 写入数据库去重（`gid` 主键），再次解析将直接复用

---

## 故障排查

- **ExHentai 401/跳转**：`EXH_COOKIE` 无效或过期；确保使用完整 Cookie 且账户可访问 ExHentai
- **Telegraph 失败**：提供 `TELEGRAPH_ACCESS_TOKEN` 更稳；或检查域名/网络
- **Telegram 403**：机器人未加入目标会话或未先与机器人对话
- **数据库连接失败**：核对 `DATABASE_URL`；未配置则会落到本地 `cache.db`

---

## 开发

- 入口：`exhenbot.__main__:main`
- 依赖管理：`uv`（`pyproject.toml` / `uv.lock`）
- 运行：`uv run -m exhenbot`
- 主要模块：
  - `exhentai_client.py`：抓取与解析、API 调用
  - `catbox_client.py`：图床上传
  - `telegraph_client.py`：页面创建
  - `storage.py`：Tortoise ORM 模型与存取
  - `config.py`：配置加载
  - `utils.py`：请求重试

---

## 许可证

[GPL-3.0-or-later](LICENSE)


