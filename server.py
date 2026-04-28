#!/usr/bin/env python3
"""RAG Knowledge Base Web UI — изолированная база знаний."""

import os
import subprocess
import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_file

app = Flask(__name__, static_folder=None)

# Isolated RAG brain paths
RAG_DIR = Path("/root/rag-brain")
SOURCES_DIR = RAG_DIR / "sources"
UPLOAD_DIR = RAG_DIR / "uploads"
META_FILE = RAG_DIR / "documents.json"

SUPPORTED_EXT = {"docx", "xlsx", "pdf", "txt", "md", "csv", "json"}

# gbrain config paths
GBRAIN_CONFIG = Path("/root/.gbrain/config.json")
GBRAIN_CONFIG_MAIN = Path("/root/.gbrain/config.main.json")
RAG_PGLITE = str(RAG_DIR / "rag.pglite")

os.environ["PATH"] = f"{Path.home() / '.bun/bin'}:{os.environ.get('PATH', '')}"


def load_meta():
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    return {}


def save_meta(meta):
    META_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def use_rag_brain():
    """Point gbrain at the RAG database."""
    GBRAIN_CONFIG.write_text(json.dumps({"engine": "pglite", "database_path": RAG_PGLITE}))


def run_gbrain(cmd):
    use_rag_brain()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, env=os.environ)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def convert_docx(input_path, output_path):
    """Convert docx using our numbering-aware converter (better than markitdown for Word docs)."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "rag-brain"))
    from convert import convert_docx as _our_convert
    _our_convert(Path(input_path), output_path, Path(input_path).stem)


def convert_xlsx(input_path, output_path):
    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(str(input_path))
    output_path.write_text(result.text_content, encoding="utf-8")


def convert_pdf(input_path, output_path):
    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(str(input_path))
    output_path.write_text(result.text_content, encoding="utf-8")


def convert_file(input_path, output_path):
    ext = Path(input_path).suffix.lower().lstrip(".")
    if ext == "docx":
        convert_docx(input_path, output_path)
    elif ext == "xlsx":
        convert_xlsx(input_path, output_path)
    elif ext == "pdf":
        convert_pdf(input_path, output_path)
    else:
        shutil.copy2(str(input_path), str(output_path))


def call_llm(prompt):
    import urllib.request
    api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY"))
    base_url = os.environ.get("LLM_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    url = f"{base_url}/chat/completions"
    system = (
        "## РОЛЬ И ЛИЧНОСТЬ\n\n"
        "Ты — **RAG Knowledge Agent**, специализированный AI-помощник для работы с базой знаний.\n\n"
        "**Твоя экспертиза:**\n"
        "- Поиск релевантной информации в базе знаний\n"
        "- Формулирование точных и обоснованных ответов\n"
        "- Минимизация галлюцинаций\n"
        "- Работа с документами и фрагментами\n\n"
        "**Твоя личность:**\n"
        "- Проактивный и внимательный к деталям\n"
        "- Методичный — сначала ищешь в базе, потом отвечаешь\n"
        "- Педагогичный — объясняешь не только \"что\", но и \"почему\"\n"
        "- Честный относительно рисков и ограничений\n\n"
        "---\n\n"
        "## ФУНДАМЕНТАЛЬНЫЙ ПРИНЦИП РАБОТЫ\n\n"
        "**⚠️ КРИТИЧЕСКИ ВАЖНО: Всегда ищи в базе знаний перед ответом**\n\n"
        "Перед ответом на ЛЮБОЙ вопрос ты ОБЯЗАН:\n\n"
        "1. **Выполнить поиск в RAG** — Получить релевантные документы\n"
        "2. **Анализировать результаты** — Проверить, достаточно ли информации, убедиться, что ответ основан на документах\n"
        "3. **Только после этого** формировать ответ\n\n"
        "Если в базе нет информации — честно скажи об этом."
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


@app.route("/")
def index():
    return send_file(Path(__file__).parent / "static" / "index.html")


@app.route("/PROJECT.md")
def project_md():
    return send_file(Path(__file__).parent / "static" / "PROJECT.md", as_attachment=True)

@app.route("/PROJECT.pdf")
def project_pdf():
    return send_file(Path(__file__).parent / "static" / "PROJECT.pdf", as_attachment=True)


@app.route("/api/documents", methods=["GET"])
def list_documents():
    meta = load_meta()
    docs = []
    for doc_id, info in meta.items():
        docs.append({
            "id": doc_id,
            "name": info["name"],
            "size": info.get("size", 0),
            "uploaded_at": info.get("uploaded_at", ""),
            "chunks": info.get("chunks", 0),
        })
    return jsonify(docs)


@app.route("/api/documents", methods=["POST"])
def upload_document():
    if "file" not in request.files:
        return jsonify({"error": "Нет файла"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Пустое имя файла"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in SUPPORTED_EXT:
        return jsonify({"error": f"Неподдерживаемый формат: {ext}. Поддерживаются: {', '.join(SUPPORTED_EXT)}"}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    doc_id = str(uuid.uuid4())[:8]
    safe_name = f"{doc_id}_{f.filename.replace(' ', '_')}"
    original_path = UPLOAD_DIR / safe_name
    f.save(str(original_path))

    # Convert to markdown
    md_name = safe_name.rsplit(".", 1)[0]
    md_path = SOURCES_DIR / f"{md_name}.md"

    try:
        convert_file(original_path, md_path)
    except Exception as e:
        return jsonify({"error": f"Ошибка конвертации: {str(e)[:300]}"}), 500

    if not md_path.exists() or md_path.stat().st_size == 0:
        return jsonify({"error": "Конвертация вернула пустой файл"}), 500

    # Import & embed
    run_gbrain(f"gbrain import {SOURCES_DIR}/ --no-embed")
    run_gbrain("gbrain embed --stale")

    # Count chunks — search by doc_id prefix (gbrain transliterates filenames, original md_name may not match)
    code, out, _ = run_gbrain(f"gbrain search '{doc_id}'")
    chunks = len([l for l in (out or "").split("\n") if l.strip().startswith("[")])

    meta = load_meta()
    meta[doc_id] = {
        "name": f.filename,
        "original_path": str(original_path),
        "md_name": md_name,
        "md_path": str(md_path),
        "size": original_path.stat().st_size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
    }
    save_meta(meta)

    return jsonify({"id": doc_id, "name": f.filename, "chunks": chunks}), 201


@app.route("/api/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    meta = load_meta()
    if doc_id not in meta:
        return jsonify({"error": "Документ не найден"}), 404

    info = meta[doc_id]

    for p in [info.get("original_path"), info.get("md_path")]:
        if p and Path(p).exists():
            Path(p).unlink()

    run_gbrain(f"gbrain delete '{info['md_name']}'")
    run_gbrain("gbrain embed --stale")

    del meta[doc_id]
    save_meta(meta)

    return jsonify({"deleted": doc_id})


@app.route("/api/search", methods=["POST"])
def search():
    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Пустой запрос"}), 400

    code, out, err = run_gbrain(f"gbrain query '{query}' --limit 15 --detail high")
    
    # Also grep sources for keyword matches (more reliable for exact terms)
    import glob, re as _re
    query_words = _re.findall(r'[а-яА-ЯёЁa-zA-Z0-9]{3,}', query)
    grep_matches = []
    grep_threshold = max(2, len(query_words) // 3)
    for src in glob.glob(str(SOURCES_DIR / "*.md")):
        with open(src, 'r') as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            hit = sum(1 for w in query_words if w.lower() in line.lower())
            if hit >= grep_threshold:
                start = max(0, i - 2)
                end = min(len(lines), i + 12)
                grep_matches.append((hit, ''.join(lines[start:end])))
    # Sort by relevance (highest hit count first), take top 3
    grep_matches.sort(key=lambda x: -x[0])
    grep_matches = [m[1] for m in grep_matches[:3]]
    
    # Always prepend grep results (they are more reliable for exact matches)
    if grep_matches:
        grep_context = '\n---\n'.join(grep_matches)
        if out and out.strip() != "No results.":
            context = grep_context + '\n---\n' + out
        else:
            context = grep_context
    elif out and out.strip() != "No results.":
        context = out
    else:
        context = "Ничего не найдено в базе знаний."

    # Clean context: remove score prefixes, replace slugs with real names
    import re
    clean_context = re.sub(r'\[\d+\.\d+\]\s*', '', context)
    meta = load_meta()
    for doc_id, info in meta.items():
        clean_context = clean_context.replace(info['md_name'], info['name'])
    doc_list = "\n".join(f"- {info['name']}" for info in meta.values())

    prompt = (
        f"Вопрос: {query}\n\n"
        f"Документы в базе знаний:\n{doc_list}\n\n"
        f"Найденные фрагменты:\n{clean_context}\n\n"
        f"Ответь на вопрос четко, информативно, без общих фраз, без повторов. "
        f"Информация должна быть релевантная и по смыслу полезная, а не просто выдержки без логики.\n"
        f"Основывайся ТОЛЬКО на этих фрагментах. Приводи конкретные факты и примеры из документов.\n"
        f"Не упоминай оценки релевантности или номера фрагментов.\n"
        f"СТРОГО ЗАПРЕЩЕНО: не придумывай информацию, код, примеры или факты, которых нет в найденных фрагментах. "
        f"Если фрагменты не содержат ответа — просто напиши: в базе знаний нет релевантной информации по этому вопросу, и предложи, что ещё можно поискать.\n\n"
        f"Формат: markdown — **жирный** для ключевых понятий, "
        f"списки с `-` для перечислений, `код` для проводок и номеров, "
        f"заголовки `##` для разделов."
    )

    try:
        answer = call_llm(prompt)
    except Exception as e:
        answer = f"Ошибка синтеза: {str(e)}"

    return jsonify({"query": query, "answer": answer, "sources": context})


if __name__ == "__main__":
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure RAG brain is initialized
    if not (RAG_DIR / "rag.pglite").exists():
        run_gbrain("gbrain init --path /root/rag-brain/rag.pglite")

    print("🧠 RAG Knowledge Base UI (isolated): http://localhost:8787")
    app.run(host="0.0.0.0", port=8787, debug=False)
