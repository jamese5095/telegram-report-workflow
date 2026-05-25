# Telegram 群聊信息报告工作流

这是一个只读的 Telegram 消息导出与报告生成工具。它可以连接你的 Telegram 账号，读取指定频道/群聊/私聊的消息，导出为 JSON，并调用大模型 API 生成中文事件报告、市场情绪报告或观点总结。

适合用途：

- 跟踪 Telegram 资讯频道过去 24 小时的重要事件
- 分析交易群、项目群、社区群的高频观点
- 定期导出群聊消息并生成 Markdown 报告
- 将 Telegram 数据留存在本地，避免手动复制粘贴

## 工作原理

整个流程分为四步：

1. 使用 Telegram Client API 登录

   脚本使用 `Telethon` 连接 Telegram。你需要先在 [my.telegram.org/apps](https://my.telegram.org/apps) 创建应用，获得 `api_id` 和 `api_hash`。

2. 只读读取消息

   脚本只调用读取接口，例如列出 dialogs、读取某个 chat 的历史消息。代码中没有发消息、删消息、改资料等写操作。

3. 导出 JSON

   指定群聊后，脚本会把消息导出为结构化 JSON，包含时间、发送者、文本、浏览量、转发数等字段。

4. 调用大模型生成报告

   如果配置了 `MODEL_API_KEY`，脚本会把消息按长度分块，先生成分段摘要，再综合成完整 Markdown 报告。如果没有配置大模型 API，会退化为基础统计报告。

## 目录结构

```text
.
├── telegram_reporter.py          # 推荐使用的完整工作流脚本
├── telegram_readonly_export.py   # 简单只读导出脚本，保留作备用
├── requirements.txt              # Python 依赖
├── .env.example                  # 配置模板
├── .gitignore                    # 防止提交密钥、session、导出数据
├── exports/                      # 生成的 JSON，默认不提交
└── reports/                      # 生成的 Markdown 报告，默认不提交
```

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

复制配置模板：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```env
TELEGRAM_API_ID=你的_app_api_id
TELEGRAM_API_HASH=你的_app_api_hash
TELEGRAM_PHONE=你的手机号
TELEGRAM_SESSION=telegram_readonly.session

MODEL_API_BASE_URL=https://api.openai.com/v1/chat/completions
MODEL_API_KEY=你的大模型_API_Key
MODEL_NAME=gpt-4.1-mini
REPORT_CHUNK_CHARS=18000
```

`TELEGRAM_PHONE` 可以不填，首次登录时脚本会提示输入。

## 获取 Telegram API

1. 打开 [https://my.telegram.org](https://my.telegram.org)
2. 用 Telegram 手机号登录
3. 进入 `API development tools`
4. 创建应用
5. 复制 `App api_id` 和 `App api_hash`

注意：`api_id` / `api_hash` 不是 BotFather 的 bot token。它们用于以用户客户端身份连接 Telegram。

## 基础使用

### 1. 列出聊天列表

```bash
python telegram_reporter.py list --limit 100
```

首次运行会要求输入 Telegram 验证码。如果账号开启了两步验证，还会要求输入二步验证密码。

登录成功后，本地会生成 session 文件，例如：

```text
telegram_readonly.session
```

后续运行通常不需要再次输入验证码。

### 2. 导出某个聊天

按 chat id 导出：

```bash
python telegram_reporter.py export --chat -1001387109317 --since-hours 24 --limit 1000
```

按用户名或标题导出：

```bash
python telegram_reporter.py export --chat theblockbeats --since-hours 24
```

参数说明：

- `--chat`：聊天 ID、频道用户名、群名或部分标题
- `--since-hours`：只导出最近 N 小时
- `--limit`：最多导出多少条消息，`0` 表示不按数量限制
- `--out`：指定 JSON 输出路径

### 3. 从 JSON 生成报告

```bash
python telegram_reporter.py report \
  --input exports/theblockbeats_20260525_120000.json \
  --out reports/blockbeats_24h_report.md
```

### 4. 一步完成导出和报告

```bash
python telegram_reporter.py run \
  --chat theblockbeats \
  --since-hours 24 \
  --limit 1000 \
  --report-out reports/blockbeats_24h_report.md
```

## 大模型 API 接口

脚本预留的是 OpenAI Chat Completions 兼容接口：

```env
MODEL_API_BASE_URL=https://api.openai.com/v1/chat/completions
MODEL_API_KEY=你的_API_Key
MODEL_NAME=gpt-4.1-mini
```

如果你使用其他 OpenAI-compatible 服务，只要它兼容 `/v1/chat/completions`，通常只需要替换：

```env
MODEL_API_BASE_URL=https://你的服务地址/v1/chat/completions
MODEL_API_KEY=你的服务密钥
MODEL_NAME=你的模型名
```

如果不配置 `MODEL_API_KEY`，脚本仍可导出消息，并生成一个基础统计报告，但不会有深度语义总结。

## 输出内容

大模型报告默认会包含：

- 摘要
- 主要事件线
- 资产 / 项目观察
- 高频观点或市场情绪
- 风险与后续跟踪事项

导出的 JSON 会包含：

- chat 信息
- 导出时间
- 消息数量
- 每条消息的时间、文本、发送者、浏览量、转发数等

## 安全注意事项

请不要提交以下文件到 GitHub：

- `.env`
- `*.session`
- `exports/`
- `reports/`

这些已经写进 `.gitignore`，但上传前仍建议检查：

```bash
git status
```

敏感性说明：

- `api_id` + `api_hash` 本身需要妥善保管。
- `telegram_readonly.session` 更敏感，它代表已经登录的 Telegram 客户端会话。
- 如果 session 泄露，别人可能读取账号可访问的聊天。
- 如需撤销授权，可删除本地 session 文件，并在 Telegram `Settings -> Devices` 中结束对应会话。

## 常见问题

### 这是 Bot API 吗？

不是。Bot API 只能读取 bot 能看到的消息，通常无法读取你的私人聊天历史。本项目使用 Telegram Client API，通过你的账号只读访问你本来能看到的聊天。

### 脚本会不会发消息？

不会。当前代码只实现了 list、export、report、run 这类读取和本地生成操作，没有发送、删除、转发或修改 Telegram 内容的逻辑。

### 为什么首次登录需要验证码？

因为脚本相当于一个新的 Telegram 客户端。登录成功后会生成本地 session 文件，以后可以复用。

### 为什么有时连接 Telegram 会失败？

常见原因包括网络不稳定、Telegram 数据中心迁移、代理/VPN 问题或旧 session 损坏。可以尝试删除：

```bash
rm -f telegram_readonly.session telegram_readonly.session-journal
```

然后重新运行登录。

### 可以定时运行吗？

可以。你可以用 cron、GitHub Actions self-hosted runner、系统任务计划或自己的服务器定时执行：

```bash
python telegram_reporter.py run --chat theblockbeats --since-hours 24 --limit 1000
```

不要把 Telegram session 或 API key 放到公开仓库。定时任务建议运行在你自己控制的机器上。
