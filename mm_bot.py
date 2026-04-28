#!/usr/bin/env python3
"""Mattermost RAG Bot — слушает канал и отвечает на вопросы через RAG."""

import os
import sys
import json
import time
import re
import threading
import urllib.request
import websocket
from pathlib import Path

MM_URL = "https://mm.sberdevices.ru"
MM_TOKEN = os.environ.get("MM_BOT_TOKEN", "")
CHANNEL_ID = "96k8it3apffj9dkh5p95agtcih"
BOT_ID = "zoe3chmktjdjbmx6wfqhojg9bw"
TEAM_ID = "8yfs9fjym7dwudxh3g6z4znefo"

# RAG API endpoint
RAG_API = os.environ.get("RAG_API_URL", "http://localhost:8787")


def mm_api(method, path, data=None):
    """Call Mattermost API v4."""
    url = f"{MM_URL}/api/v4{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {MM_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:200]}


def rag_query(question):
    """Send question to RAG API and return answer."""
    url = f"{RAG_API}/api/search"
    body = json.dumps({"query": question}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data.get("answer", data.get("error", "Не удалось получить ответ"))


def post_message(text, root_id=None):
    """Post a message to the channel."""
    data = {
        "channel_id": CHANNEL_ID,
        "message": text,
    }
    if root_id:
        data["root_id"] = root_id
    return mm_api("POST", "/posts", data)


def format_answer(answer):
    """Format RAG answer for Mattermost (markdown supported)."""
    # Mattermost supports markdown natively, but trim very long responses
    if len(answer) > 4000:
        answer = answer[:3950] + "\n\n... _(ответ обрезан)_"
    return answer


# Deduplication: track processed post IDs to avoid duplicate responses
_processed_posts = {}
_PROCESSED_TTL = 300  # seconds to remember processed posts


def _cleanup_processed():
    """Remove expired entries from processed posts cache."""
    now = time.time()
    expired = [k for k, v in _processed_posts.items() if now - v > _PROCESSED_TTL]
    for k in expired:
        del _processed_posts[k]


def handle_post(post):
    """Process an incoming post."""
    user_id = post.get("user_id", "")
    message = post.get("message", "").strip()
    post_id = post.get("id", "")
    root_id = post.get("root_id", "") or post_id

    # Ignore own messages
    if user_id == BOT_ID:
        return

    # Only process messages in our channel
    if post.get("channel_id") != CHANNEL_ID:
        return

    # Skip empty messages
    if not message:
        return

    # Deduplicate: skip if we already processed this post
    _cleanup_processed()
    if post_id in _processed_posts:
        print(f"[MM] Duplicate post {post_id}, skipping")
        return
    _processed_posts[post_id] = time.time()

    # Debug: log all incoming messages with their content
    print(f"[MM] Incoming: post={post_id} user={user_id} msg={message[:200]!r}")

    # Only respond when bot is explicitly mentioned via @cloud_rag_doc
    # Mattermost mention format: @(username)[server_id](/link/BOT_ID)
    mention_patterns = [
        r"@cloud_rag_doc",                        # plain mention
        rf"\(https?://[^)]*{BOT_ID}[^)]*\)",     # mention link
        rf"@(?:Cloud\s*RAG\s*Doc)\b",              # display name variant
    ]
    mentioned = any(re.search(p, message) for p in mention_patterns)

    if not mentioned:
        return

    # Strip the mention from the message
    query = message
    query = re.sub(r"@cloud_rag_doc\s*", "", query)
    for p in mention_patterns[1:]:
        query = re.sub(p, "", query)
    # Remove leftover @(Name) part
    query = re.sub(r"@\([^)]+\)", "", query)
    query = query.strip()

    if not query:
        post_message("Задайте вопрос — я найду ответ в базе знаний.", root_id=root_id)
        return

    print(f"[RAG] Question from {user_id}: {query[:100]}")

    try:
        rag_answer = rag_query(query)
        answer = format_answer(rag_answer)
    except Exception as e:
        answer = f"⚠️ Ошибка при поиске: {str(e)[:200]}"

    post_message(answer, root_id=root_id)
    print(f"[RAG] Answered ({len(answer)} chars)")


def on_message(ws, message):
    """Handle WebSocket message."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    event = data.get("event", "")

    if event == "posted":
        post_data = data.get("data", {}).get("post", "")
        if isinstance(post_data, str):
            try:
                post = json.loads(post_data)
            except json.JSONDecodeError:
                return
        else:
            post = post_data
        handle_post(post)

    elif event == "hello":
        print("[MM] Connected to WebSocket")

    elif event == "channel_viewed":
        pass  # ignore


def on_error(ws, error):
    print(f"[MM] WebSocket error: {error}")


def on_close(ws, close_status, close_msg):
    print(f"[MM] WebSocket closed: {close_status} {close_msg}")


def _keepalive(ws, interval=60):
    """Send periodic WebSocket pings to keep connection alive."""
    while ws.sock:
        try:
            ws.sock.ping()
            time.sleep(interval)
        except Exception:
            break


def on_open(ws):
    print("[MM] WebSocket connected, listening for messages...")
    t = threading.Thread(target=_keepalive, args=(ws,), daemon=True)
    t.start()


def connect_websocket():
    """Connect to Mattermost WebSocket and listen for events."""
    ws_url = MM_URL.replace("https://", "wss://").replace("http://", "ws://")
    ws_url += "/api/v4/websocket"

    while True:
        try:
            ws = websocket.WebSocketApp(
                ws_url,
                header={"Authorization": f"Bearer {MM_TOKEN}"},
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10, skip_utf8_validation=True)
        except Exception as e:
            print(f"[MM] Connection error: {e}")

        print("[MM] Reconnecting in 5s...")
        time.sleep(5)


if __name__ == "__main__":
    print("🧠 Mattermost RAG Bot starting...")
    print(f"   Channel: {CHANNEL_ID}")
    print(f"   RAG API: {RAG_API}")
    connect_websocket()
