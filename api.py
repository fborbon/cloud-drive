"""Cloud Drive pre-signed URL API — runs as a separate service on port 8506."""

import io
import mimetypes
import os
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import boto3
import yaml
from flask import Flask, jsonify, request, Response, stream_with_context

app = Flask(__name__)

CONFIG_SEARCH = [
    Path(__file__).parent / "config.yaml",
    Path.home() / ".cloud-drive" / "config.yaml",
]

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _load_config() -> dict:
    cfg: dict = {"bucket": None, "region": REGION}
    for p in CONFIG_SEARCH:
        if p.exists():
            with open(p) as f:
                cfg.update(yaml.safe_load(f) or {})
            break
    if os.environ.get("CLOUD_DRIVE_BUCKET"):
        cfg["bucket"] = os.environ["CLOUD_DRIVE_BUCKET"]
    return cfg


CFG = _load_config()

INDEX_DB = Path(os.environ.get("CLOUD_DRIVE_INDEX", "~/.cloud-drive/index.db")).expanduser()
_tree_cache: dict = {"json": None, "ts": 0}
_TREE_TTL = 120  # seconds


def _build_tree_json() -> str:
    import json
    prefix = (CFG.get("s3_prefix") or "seagate/Personal").rstrip("/") + "/"
    conn = sqlite3.connect(str(INDEX_DB))
    rows = conn.execute("SELECT s3_key, size, synced_at FROM files").fetchall()
    conn.close()

    tree: dict = {}

    def node(path: str) -> dict:
        if path not in tree:
            tree[path] = {"dirs": {}, "files": []}
        return tree[path]

    for key, size, synced in rows:
        rel = key[len(prefix):] if key.startswith(prefix) else key
        if not rel:
            continue
        parts = rel.rstrip("/").split("/")
        fname = parts[-1]
        fparts = parts[:-1]
        fpath = "/".join(fparts)
        node(fpath)["files"].append({"n": fname, "s": size, "d": (synced or "")[:10], "k": key})
        for i in range(len(fparts)):
            parent = "/".join(fparts[:i]) if i > 0 else ""
            child = fparts[i]
            d = node(parent)["dirs"]
            if child not in d:
                d[child] = {"b": 0, "c": 0}
            d[child]["b"] += size
            d[child]["c"] += 1
            node("/".join(fparts[:i + 1]))

    return json.dumps(tree, separators=(",", ":"))


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/presign")
def presign():
    key = request.args.get("key", "")
    dl  = request.args.get("dl", "0") == "1"
    if not key:
        return jsonify({"error": "missing key"}), 400
    if not CFG.get("bucket"):
        return jsonify({"error": "bucket not configured"}), 500
    try:
        client = boto3.client("s3", region_name=CFG.get("region", "us-east-1"))
        fname = Path(key).name
        mime, _ = mimetypes.guess_type(fname)
        params: dict = {"Bucket": CFG["bucket"], "Key": key}
        if dl:
            params["ResponseContentDisposition"] = f'attachment; filename="{fname}"'
        else:
            params["ResponseContentDisposition"] = f'inline; filename="{fname}"'
            if mime:
                params["ResponseContentType"] = mime
        url = client.generate_presigned_url("get_object", Params=params, ExpiresIn=3600)
        return jsonify({"url": url})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/transcode")
def transcode():
    """Stream-transcode a WMA file to MP3 via ffmpeg without buffering the full output."""
    key = request.args.get("key", "")
    if not key:
        return jsonify({"error": "missing key"}), 400
    if not CFG.get("bucket"):
        return jsonify({"error": "bucket not configured"}), 500
    try:
        client = boto3.client("s3", region_name=CFG.get("region", "us-east-1"))
        obj = client.get_object(Bucket=CFG["bucket"], Key=key)
        wma_stream = obj["Body"]
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0",
         "-vn", "-acodec", "libmp3lame", "-q:a", "4",
         "-f", "mp3", "pipe:1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    import threading
    def feed():
        try:
            for chunk in wma_stream.iter_chunks(chunk_size=64 * 1024):
                proc.stdin.write(chunk)
        finally:
            proc.stdin.close()

    threading.Thread(target=feed, daemon=True).start()

    fname = Path(key).stem + ".mp3"
    return Response(
        stream_with_context(iter(lambda: proc.stdout.read(32 * 1024), b"")),
        mimetype="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


@app.route("/pdf")
def pdf_proxy():
    """Proxy a PDF from S3 through the API (same-origin) so PDF.js can fetch it."""
    key = request.args.get("key", "")
    if not key:
        return jsonify({"error": "missing key"}), 400
    if not CFG.get("bucket"):
        return jsonify({"error": "bucket not configured"}), 500
    try:
        client = boto3.client("s3", region_name=CFG.get("region", "us-east-1"))
        obj = client.get_object(Bucket=CFG["bucket"], Key=key)
        return Response(
            stream_with_context(iter(lambda: obj["Body"].read(64 * 1024), b"")),
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{Path(key).name}"',
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/content")
def content():
    key = request.args.get("key", "")
    if not key:
        return jsonify({"error": "missing key"}), 400
    if not CFG.get("bucket"):
        return jsonify({"error": "bucket not configured"}), 500
    try:
        client = boto3.client("s3", region_name=CFG.get("region", "us-east-1"))
        obj = client.get_object(Bucket=CFG["bucket"], Key=key)
        text = obj["Body"].read(512 * 1024).decode("utf-8", errors="replace")
        return text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/tree")
def tree():
    now = time.time()
    if _tree_cache["json"] is None or now - _tree_cache["ts"] > _TREE_TTL:
        try:
            _tree_cache["json"] = _build_tree_json()
            _tree_cache["ts"] = now
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
    return Response(_tree_cache["json"], mimetype="application/json",
                    headers={"Cache-Control": f"max-age={_TREE_TTL}"})


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8506, debug=False)
