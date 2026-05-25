#!/usr/bin/env python3
"""
Telegram read-only exporter and report generator.

The script intentionally supports only read operations:
  - list dialogs
  - export messages
  - generate a report from exported messages

Configuration is read from environment variables or a local .env file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import Message


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / "exports"
REPORT_DIR = BASE_DIR / "reports"


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int | None = None) -> int | None:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer.") from exc


@dataclass
class TelegramConfig:
    api_id: int
    api_hash: str
    session: Path
    phone: str | None


@dataclass
class ModelConfig:
    api_base_url: str
    api_key: str | None
    model: str
    chunk_chars: int


def get_telegram_config() -> TelegramConfig:
    api_id = env_int("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if api_id is None:
        raw = input("Telegram api_id: ").strip()
        if not raw.isdigit():
            raise SystemExit("Telegram api_id must be a number.")
        api_id = int(raw)
    if not api_hash:
        api_hash = getpass("Telegram api_hash: ").strip()
    if not api_hash:
        raise SystemExit("Telegram api_hash is required.")

    session = Path(os.environ.get("TELEGRAM_SESSION", "telegram_readonly.session"))
    if not session.is_absolute():
        session = BASE_DIR / session
    return TelegramConfig(
        api_id=api_id,
        api_hash=api_hash,
        session=session,
        phone=os.environ.get("TELEGRAM_PHONE"),
    )


def get_model_config() -> ModelConfig:
    return ModelConfig(
        api_base_url=os.environ.get(
            "MODEL_API_BASE_URL", "https://api.openai.com/v1/chat/completions"
        ),
        api_key=os.environ.get("MODEL_API_KEY"),
        model=os.environ.get("MODEL_NAME", "gpt-4.1-mini"),
        chunk_chars=env_int("REPORT_CHUNK_CHARS", 18000) or 18000,
    )


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value).strip("._-")
    return value[:90] or "telegram_chat"


def message_to_dict(message: Message) -> dict[str, Any]:
    sender = getattr(message, "sender", None)
    sender_name = None
    if sender:
        first = getattr(sender, "first_name", None) or ""
        last = getattr(sender, "last_name", None) or ""
        sender_name = " ".join(part for part in (first, last) if part) or None

    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "sender_id": message.sender_id,
        "sender_name": sender_name,
        "text": message.message,
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "outgoing": bool(message.out),
        "reply_to_msg_id": getattr(message.reply_to, "reply_to_msg_id", None),
        "has_media": message.media is not None,
    }


async def make_client(config: TelegramConfig) -> TelegramClient:
    client = TelegramClient(str(config.session), config.api_id, config.api_hash)
    await client.connect()
    if await client.is_user_authorized():
        return client

    phone = config.phone or input("Telegram phone (+countrycode...): ").strip()
    await client.start(phone=phone)
    return client


async def list_dialogs(limit: int) -> None:
    config = get_telegram_config()
    client = await make_client(config)
    try:
        print("dialog_id\ttype\ttitle\tusername")
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            username = getattr(entity, "username", None) or ""
            print(f"{dialog.id}\t{type(entity).__name__}\t{dialog.name}\t{username}")
    finally:
        await client.disconnect()


async def resolve_chat(client: TelegramClient, chat: str) -> Any:
    try:
        return await client.get_entity(int(chat))
    except ValueError:
        pass
    except Exception:
        pass

    try:
        return await client.get_entity(chat)
    except Exception:
        pass

    needle = chat.lower()
    matches = []
    async for dialog in client.iter_dialogs(limit=None):
        name = dialog.name or ""
        username = getattr(dialog.entity, "username", None) or ""
        if needle in name.lower() or needle == username.lower():
            matches.append(dialog)

    if not matches:
        raise SystemExit(f"No chat found matching: {chat}")
    if len(matches) > 1:
        print("Multiple chats matched. Re-run with one of these dialog ids:")
        for dialog in matches[:30]:
            print(f"{dialog.id}\t{dialog.name}")
        raise SystemExit(2)
    return matches[0].entity


async def export_messages(
    chat: str,
    limit: int,
    since_hours: int | None,
    out: str | None,
) -> Path:
    config = get_telegram_config()
    client = await make_client(config)
    try:
        entity = await resolve_chat(client, chat)
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat)
        since = None
        if since_hours:
            since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

        messages = []
        max_messages = None if limit == 0 else limit
        async for message in client.iter_messages(entity, limit=max_messages):
            if since and message.date and message.date < since:
                break
            if message.message:
                messages.append(message_to_dict(message))
        messages.reverse()

        payload = {
            "chat": {
                "id": getattr(entity, "id", None),
                "title": title,
                "username": getattr(entity, "username", None),
                "type": type(entity).__name__,
            },
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "since_hours": since_hours,
            "message_count": len(messages),
            "messages": messages,
        }

        if out:
            out_path = Path(out).expanduser()
            if not out_path.is_absolute():
                out_path = BASE_DIR / out_path
        else:
            EXPORT_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = EXPORT_DIR / f"{safe_filename(title)}_{stamp}.json"

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path
    finally:
        await client.disconnect()


def compact_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        text = (msg.get("text") or "").replace("\r", " ").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        sender = msg.get("sender_name") or msg.get("sender_id") or ""
        lines.append(f"[{msg.get('date')}] {sender}: {text}")
    return "\n".join(lines)


def split_chunks(text: str, max_chars: int) -> list[str]:
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def call_openai_compatible(config: ModelConfig, messages: list[dict[str, str]]) -> str:
    if not config.api_key:
        raise RuntimeError("MODEL_API_KEY is not set.")
    body = json.dumps(
        {
            "model": config.model,
            "messages": messages,
            "temperature": 0.2,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        config.api_base_url,
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API request failed: HTTP {exc.code}: {details}") from exc

    try:
        return payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected LLM API response: {payload}") from exc


def heuristic_report(data: dict[str, Any]) -> str:
    messages = [m for m in data.get("messages", []) if m.get("text")]
    text = "\n".join(m["text"] for m in messages)
    latin_tokens = re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,20}", text)
    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    stop = {
        "消息",
        "原文链接",
        "表示",
        "目前",
        "美元",
        "已经",
        "今日",
        "过去",
        "市场",
        "用户",
        "数据",
    }
    terms = Counter(t for t in latin_tokens + chinese_terms if t not in stop)
    top = terms.most_common(40)
    first = messages[0]["date"] if messages else "N/A"
    last = messages[-1]["date"] if messages else "N/A"
    title = data.get("chat", {}).get("title") or data.get("chat", {}).get("username") or "Telegram"

    bullets = "\n".join(f"- {term}: {count}" for term, count in top)
    samples = "\n".join(
        f"- {m.get('date')}: {re.sub(r'\\s+', ' ', m.get('text', '')).strip()[:180]}"
        for m in messages[:20]
    )
    return f"""# {title} 消息报告

> 未配置 `MODEL_API_KEY`，以下为基础统计报告。配置大模型 API 后可生成语义综合报告。

## 数据范围

- 消息数：{len(messages)}
- 起始时间：{first}
- 结束时间：{last}

## 高频词

{bullets}

## 前 20 条消息样例

{samples}
"""


def llm_report(data: dict[str, Any], output_language: str) -> str:
    config = get_model_config()
    messages_text = compact_messages(data.get("messages", []))
    chunks = split_chunks(messages_text, config.chunk_chars)
    chat = data.get("chat", {})
    title = chat.get("title") or chat.get("username") or "Telegram chat"

    system = (
        "你是一个严谨的信息分析助手。你会把 Telegram 消息整理为事实清晰、"
        "观点分层、避免夸大、适合投资/研究复盘的中文报告。不要编造消息中没有的信息。"
    )

    partials = []
    for index, chunk in enumerate(chunks, start=1):
        prompt = f"""请分析以下 Telegram 消息片段，这是第 {index}/{len(chunks)} 段。

要求：
- 提取主要事件、项目、资产、人物或机构。
- 合并重复消息。
- 区分事实、市场观点、风险信号。
- 输出简洁结构化要点。

消息：
{chunk}
"""
        partials.append(
            call_openai_compatible(
                config,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
        )

    final_prompt = f"""请把以下分段分析综合成一份完整报告。

报告对象：{title}
消息数量：{data.get('message_count')}
导出时间：{data.get('exported_at')}
输出语言：{output_language}

报告结构建议：
1. 摘要
2. 主要事件线
3. 资产/项目观察
4. 高频观点或市场情绪
5. 风险与后续跟踪事项

分段分析：
{chr(10).join(f'--- 分段 {i+1} ---{chr(10)}{part}' for i, part in enumerate(partials))}
"""
    return call_openai_compatible(
        config,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": final_prompt},
        ],
    )


def generate_report(input_path: str, out: str | None, output_language: str) -> Path:
    data_path = Path(input_path).expanduser()
    if not data_path.is_absolute():
        data_path = BASE_DIR / data_path
    data = json.loads(data_path.read_text(encoding="utf-8"))

    model_config = get_model_config()
    if model_config.api_key:
        report = llm_report(data, output_language)
    else:
        report = heuristic_report(data)

    if out:
        out_path = Path(out).expanduser()
        if not out_path.is_absolute():
            out_path = BASE_DIR / out_path
    else:
        REPORT_DIR.mkdir(exist_ok=True)
        title = data.get("chat", {}).get("title") or "telegram_report"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = REPORT_DIR / f"{safe_filename(title)}_{stamp}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report + "\n", encoding="utf-8")
    return out_path


async def export_and_report(args: argparse.Namespace) -> None:
    export_path = await export_messages(args.chat, args.limit, args.since_hours, args.export_out)
    report_path = generate_report(str(export_path), args.report_out, args.language)
    print(f"Exported messages: {export_path}")
    print(f"Generated report: {report_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Telegram report workflow.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List Telegram dialogs.")
    list_parser.add_argument("--limit", type=int, default=100)

    export_parser = sub.add_parser("export", help="Export messages from one chat.")
    export_parser.add_argument("--chat", required=True, help="Dialog id, username, or chat title.")
    export_parser.add_argument("--limit", type=int, default=1000, help="0 means no count limit.")
    export_parser.add_argument("--since-hours", type=int, help="Only export messages in recent N hours.")
    export_parser.add_argument("--out", help="Output JSON path.")

    report_parser = sub.add_parser("report", help="Generate a report from an exported JSON file.")
    report_parser.add_argument("--input", required=True, help="Exported JSON path.")
    report_parser.add_argument("--out", help="Output Markdown path.")
    report_parser.add_argument("--language", default="中文")

    run_parser = sub.add_parser("run", help="Export a chat and immediately generate a report.")
    run_parser.add_argument("--chat", required=True, help="Dialog id, username, or chat title.")
    run_parser.add_argument("--limit", type=int, default=1000, help="0 means no count limit.")
    run_parser.add_argument("--since-hours", type=int, default=24)
    run_parser.add_argument("--export-out", help="Output JSON path.")
    run_parser.add_argument("--report-out", help="Output Markdown path.")
    run_parser.add_argument("--language", default="中文")

    return parser


async def async_main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "list":
        await list_dialogs(args.limit)
    elif args.command == "export":
        out = await export_messages(args.chat, args.limit, args.since_hours, args.out)
        print(f"Exported messages: {out}")
    elif args.command == "report":
        out = generate_report(args.input, args.out, args.language)
        print(f"Generated report: {out}")
    elif args.command == "run":
        await export_and_report(args)
    else:
        parser.print_help()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
