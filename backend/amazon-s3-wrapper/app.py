import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["*"])

S3_BASE_URL = os.environ.get(
    "S3_BASE_URL",
    "https://smuedu-dev.outsystemsenterprise.com/SMULab_AmazonS3/rest/AmazonS3",
).rstrip("/")
S3_X_CONTACTS_KEY = os.environ.get("S3_X_CONTACTS_KEY", "4e46111f-f4a9-443b-bc63-cd0d52437c04")

S3_HEADERS = {
    "Content-Type": "application/json",
    "X-Contacts-Key": S3_X_CONTACTS_KEY,
}


def _proxy(path: str) -> tuple:
    body = request.get_json(silent=True) or {}
    try:
        resp = requests.post(
            f"{S3_BASE_URL}/{path}",
            json=body,
            headers=S3_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        return jsonify(resp.json()), resp.status_code
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        detail = ""
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text if exc.response is not None else str(exc)
        return jsonify({"error": "S3 upstream error", "detail": detail}), status
    except requests.RequestException as exc:
        return jsonify({"error": "S3 upstream unreachable", "detail": str(exc)}), 502


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "amazon-s3-wrapper"})


@app.route("/UploadFile", methods=["POST"])
def upload_file():
    return _proxy("UploadFile")


@app.route("/FetchFileUrl", methods=["POST"])
def fetch_file_url():
    return _proxy("FetchFileUrl")


@app.route("/DeleteFile", methods=["POST"])
def delete_file():
    return _proxy("DeleteFile")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5022)
