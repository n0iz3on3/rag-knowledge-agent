# RAG Knowledge Agent

Веб-интерфейс и Mattermost-бот для базы знаний с RAG (Retrieval-Augmented Generation). Загрузка документов, автоматическая индексация, семантический поиск и ответы на вопросы через LLM.

## Архитектура

```
┌──────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Web UI (Flask)  │────▶│   gbrain     │────▶│   LLM API   │
│  localhost:8787   │     │  (pglite)    │     │  (OpenAI)   │
└──────────────────┘     └──────┬───────┘     └─────────────┘
                                │
┌──────────────────┐           │
│  Mattermost Bot  │───────────┘
│  (WebSocket)     │
└──────────────────┘
```

### Компоненты

- **`server.py`** — Flask-сервер с веб-интерфейсом. REST API для загрузки, удаления и поиска документов. Конвертирует docx/xlsx/pdf/txt/md/csv/json в markdown, импортирует в gbrain, генерирует эмбеддинги, синтезирует ответ через LLM.
- **`mm_bot.py`** — Mattermost-бот на WebSocket. Слушает канал, реагирует на упоминания `@cloud_rag_doc`, отправляет вопрос в RAG API и возвращает ответ в тред.
- **`static/index.html`** — Веб-интерфейс: drag-and-drop загрузка файлов, список документов, поиск с markdown-рендерингом ответов.

### Стек

- **Python 3** + Flask
- **gbrain** — локальная база знаний с pgvector-эмбеддингами (pglite)
- **markitdown** — конвертация xlsx/pdf в markdown
- **кастомный docx-конвертер** — нумерация и форматирование Word-документов
- **websocket-client** — Mattermost WebSocket
- **marked.js** — рендеринг markdown в браузере

## Возможности

- 📄 Загрузка документов (docx, xlsx, pdf, txt, md, csv, json)
- 🔍 Семантический поиск по базе знаний
- 🤖 Ответы через LLM с контекстом из найденных фрагментов
- 💬 Mattermost-бот — ответы на вопросы в канале по упоминанию
- 🗑️ Удаление документов с очисткой индекса
- 📊 Подсчёт чанков для каждого документа

## Установка

### Зависимости

```bash
pip install flask markitdown websocket-client
```

[gbrain](https://github.com/n0iz3on3/gbrain) — утилита для управления локальной базой знаний:

```bash
bun install -g gbrain
```

### Переменные окружения

```bash
# LLM для генерации ответов
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://api.proxyapi.ru/openai/v1"  # или другой OpenAI-совместимый API
export LLM_MODEL="gpt-4o-mini"

# Mattermost бот (для mm_bot.py)
export MM_BOT_TOKEN="your-bot-token"
export RAG_API_URL="http://localhost:8787"
```

### Конфигурация Mattermost бота

В `mm_bot.py` укажите параметры подключения:

```python
MM_URL = "https://your-mattermost.example.com"
CHANNEL_ID = "your-channel-id"
BOT_ID = "your-bot-user-id"
TEAM_ID = "your-team-id"
```

### Запуск

```bash
# Веб-интерфейс
python server.py

# Mattermost бот
python mm_bot.py
```

### Systemd

Пример unit-файла для Mattermost бота:

```ini
[Unit]
Description=Mattermost RAG Bot
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -u /path/to/mm_bot.py
WorkingDirectory=/path/to/rag-ui
Environment=MM_BOT_TOKEN=your-token
Environment=RAG_API_URL=http://localhost:8787
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## API

### GET /api/documents
Список загруженных документов.

### POST /api/documents
Загрузка документа (multipart/form-data, поле `file`).

### DELETE /api/documents/:id
Удаление документа по ID.

### POST /api/search
Поиск по базе знаний. Body: `{"query": "вопрос"}`. Возвращает `{"query", "answer", "sources"}`.

## Лицензия

MIT
