#!/usr/bin/env python3
"""
Read-only Telegram chat exporter.

Credentials are prompted at runtime or read from environment variables:
  TELEGRAM_API_ID
  TELEGRAM_API_HASH
  TELEGRAM_PHONE
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import Message


BASE_DIR = Path(__file__).resolve().parent
SESSION_PATH = BASE_DIR / "telegram_readonly.session"
EXPORT_DIR = BASE_DIR / "exports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List dialogs or export messages from one Telegram chat."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List recent dialogs so you can choose a chat.",
    )
    parser.add_argument(
        "--chat",
        help="Chat id, username, phone, or exact/partial chat title to export.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum messages to export. Use 0 for all available messages.",
    )
    parser.add_argument(
        "--out",
        help="Output JSON file path. Defaults to exports/<chat>_<timestamp>.json.",
    )
    return parser.parse_args()


def prompt_config() -> tuple[int, str, str]:
    api_id_raw = os.environ.get("TELEGRAM_API_ID") or input("Telegram api_id: ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or getpass("Telegram api_hash: ").strip()
    phone = os.environ.get("TELEGRAM_PHONE") or input("Telegram phone (+countrycode...): ").strip()

    if not api_id_raw.isdigit():
        raise SystemExit("api_id must be a number.")
    if not api_hash:
        raise SystemExit("api_hash is required.")
    if not phone:
        raise SystemExit("phone is required.")

    return int(api_id_raw), api_hash, phone


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value[:80] or "telegram_chat"


def message_to_dict(message: Message) -> dict[str, Any]:
    sender = getattr(message, "sender", None)
    sender_name = None
    if sender:
        first_name = getattr(sender, "first_name", None) or ""
        last_name = getattr(sender, "last_name", None) or ""
        sender_name = " ".join(part for part in (first_name, last_name) if part) or None

    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "sender_id": message.sender_id,
        "sender_name": sender_name,
        "text": message.message,
        "outgoing": bool(message.out),
        "reply_to_msg_id": getattr(message.reply_to, "reply_to_msg_id", None),
        "has_media": message.media is not None,
    }


async def list_dialogs(client: TelegramClient) -> None:
    print("Recent dialogs:")
    async for dialog in client.iter_dialogs(limit=100):
        entity = dialog.entity
        username = getattr(entity, "username", None)
        kind = type(entity).__name__
        username_text = f" @{username}" if username else ""
        print(f"{dialog.id}\t{kind}\t{dialog.name}{username_text}")


async def find_chat(client: TelegramClient, chat: str) -> Any:
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

    chat_lower = chat.lower()
    matches = []
    async for dialog in client.iter_dialogs(limit=None):
        name = dialog.name or ""
        username = getattr(dialog.entity, "username", None) or ""
        if chat_lower in name.lower() or chat_lower == username.lower():
            matches.append(dialog)

    if not matches:
        raise SystemExit(f"No chat found matching: {chat}")
    if len(matches) > 1:
        print("Multiple chats matched. Re-run with one of these ids:")
        for dialog in matches[:20]:
            print(f"{dialog.id}\t{dialog.name}")
        raise SystemExit(2)

    return matches[0].entity


async def export_chat(client: TelegramClient, chat: str, limit: int, out: str | None) -> Path:
    entity = await find_chat(client, chat)
    title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(getattr(entity, "id", chat))
    max_messages = None if limit == 0 else limit

    messages = []
    async for message in client.iter_messages(entity, limit=max_messages):
        messages.append(message_to_dict(message))

    messages.reverse()
    exported_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "exported_at": exported_at,
        "chat": {
            "id": getattr(entity, "id", None),
            "title": title,
            "username": getattr(entity, "username", None),
            "type": type(entity).__name__,
        },
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


async def main() -> None:
    args = parse_args()
    if not args.list and not args.chat:
        raise SystemExit("Use --list to list chats, or --chat <name-or-id> to export one chat.")

    api_id, api_hash, phone = prompt_config()
    client = TelegramClient(str(SESSION_PATH), api_id, api_hash)

    await client.start(phone=phone)
    try:
        if args.list:
            await list_dialogs(client)
        if args.chat:
            out_path = await export_chat(client, args.chat, args.limit, args.out)
            print(f"Exported to: {out_path}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
