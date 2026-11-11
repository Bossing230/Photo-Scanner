#!/usr/bin/env python3
"""
Photo Organizer - duplicate finder + AI category API

Local:   pip install -r requirements.txt
         python app.py            then open http://127.0.0.1:5000
Render:  start command  ->  gunicorn app:app --timeout 120
         environment    ->  GEMINI_API_KEY = your key

Endpoints
    POST /api/duplicates   (multipart: files + manifest)
    POST /api/classify     (multipart: image)   -> see classify_api.py
"""

import hashlib
import json
import os
import re
from collections import defaultdict

from flask import Flask, jsonify, request, send_from_directory

from classify_api import bp as classify_bp

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
HERE = os.path.dirname(os.path.abspath(__file__))

# Only these sites may call this API from a browser.
# If your GitHub Pages address is different, change the first line.
ALLOWED_ORIGINS = {
    "https://bossing230.github.io",
    "http://127.0.0.1:5000",
    "http://localhost:5000",
}

app = Flask(__name__)
app.register_blueprint(classify_bp)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB per request


@app.after_request
def allow_other_origins(response):
    """Lets the GitHub Pages site (and local runs) call this API."""
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    # Chrome asks for this when a public https page talks to localhost
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def find_page():
    """index.html next to this file. If a download renamed it (index(1).html ...), use that."""
    if os.path.isfile(os.path.join(HERE, "index.html")):
        return "index.html"
    for name in sorted(os.listdir(HERE)):
        if name.lower().endswith(".html") and "index" in name.lower():
            return name
    return None


@app.route("/")
def home():
    page = find_page()
    if page is None:
        return (
            "<h2>index.html not found</h2>"
            f"<p>Put <b>index.html</b> in this folder, next to app.py:</p><p><code>{HERE}</code></p>"
            "<p>Then refresh this page.</p>",
            404,
        )
    return send_from_directory(HERE, page)


@app.route("/health")
def health():
    """Handy for checking that the server is awake."""
    return jsonify(ok=True)


def file_hash(stream, chunk=1024 * 1024):
    """SHA-256 of an uploaded file, read in pieces so big photos don't fill memory."""
    h = hashlib.sha256()
    stream.seek(0)
    while block := stream.read(chunk):
        h.update(block)
    stream.seek(0)
    return h.hexdigest()


def stream_size(stream):
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def looks_like_copy(name):
    """'photo - Copy.jpg', 'Copy of photo.jpg', 'photo (1).jpg', 'photo_1.jpg' look like copies."""
    base = os.path.splitext(name)[0]
    return 1 if re.search(r"(^copy of |[ _-]copy( ?\(\d+\))?$| \(\d+\)$|_\d+$)", base, re.I) else 0


def keeper_order(item):
    """Sort key: names that don't look like copies, then shorter name, then older file, then path."""
    return (looks_like_copy(item["name"]), len(item["name"]), item["last_modified"], item["path"])


@app.route("/api/duplicates", methods=["POST", "OPTIONS"])
def duplicates():
    if request.method == "OPTIONS":
        return ("", 204)

    uploads = request.files.getlist("files")
    try:
        manifest = json.loads(request.form.get("manifest", "[]"))
    except ValueError:
        return jsonify(error="The manifest is not valid JSON."), 400
    if len(manifest) != len(uploads):
        return jsonify(error="The manifest and the files do not match."), 400

    # 1. group by size
    by_size = defaultdict(list)
    for upload, info in zip(uploads, manifest):
        path = str(info.get("path") or upload.filename)
        name = os.path.basename(path)
        if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
            continue
        item = {
            "path": path,
            "name": name,
            "size": stream_size(upload.stream),
            "last_modified": int(info.get("lastModified") or 0),
            "stream": upload.stream,
        }
        by_size[item["size"]].append(item)
    total = sum(len(items) for items in by_size.values())

    # 2. hash only the sizes that appear more than once
    by_hash = defaultdict(list)
    for items in by_size.values():
        if len(items) > 1:
            for item in items:
                by_hash[file_hash(item["stream"])].append(item)

    # 3. build the groups
    groups = []
    for digest, items in by_hash.items():
        if len(items) < 2:
            continue
        items.sort(key=keeper_order)
        groups.append({
            "hash": digest[:12],
            "size": items[0]["size"],
            "files": [
                {"path": it["path"], "name": it["name"], "size": it["size"],
                 "last_modified": it["last_modified"], "keep": i == 0}
                for i, it in enumerate(items)
            ],
        })
    groups.sort(key=lambda g: g["files"][0]["path"])

    extras = sum(len(g["files"]) - 1 for g in groups)
    wasted = sum(g["size"] * (len(g["files"]) - 1) for g in groups)
    return jsonify(total_files=total, groups=groups, extra_copies=extras, wasted_bytes=wasted)


if __name__ == "__main__":
    print(f"Folder: {HERE}")
    print(f"Page:   {find_page() or 'NOT FOUND - put index.html in the folder above'}")
    print("Open http://127.0.0.1:5000 in Chrome or Edge")
    app.run(host="127.0.0.1", port=5000)