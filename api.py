"""Cloud Drive pre-signed URL API — runs as a separate service on port 8506."""

import os
from pathlib import Path

import boto3
import yaml
from flask import Flask, jsonify, request

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
        params: dict = {"Bucket": CFG["bucket"], "Key": key}
        if dl:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{Path(key).name}"'
            )
        url = client.generate_presigned_url("get_object", Params=params, ExpiresIn=3600)
        return jsonify({"url": url})
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


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8506, debug=False)
