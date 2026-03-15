import json
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from openai import OpenAI

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


def client():
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def generate(system_prompt, user_prompt):
    resp = client().chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content or ""


@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "openai-wrapper"})


@app.route("/openai/summarise-notes", methods=["POST"])
def summarise_notes():
    body = request.get_json(silent=True) or {}
    notes = body.get("notes")
    if not notes:
        return jsonify({"error": "notes is required"}), 400

    summary = generate(
        "You are a clinical assistant. Given a doctor's raw consultation notes, produce a structured summary with: Chief Complaint, Findings, Diagnosis, Treatment Plan. Be concise. Do not invent information not in the notes.",
        notes,
    )
    return jsonify({"summary": summary})


@app.route("/openai/recommend-medications", methods=["POST"])
def recommend_medications():
    body = request.get_json(silent=True) or {}
    diagnosis = body.get("diagnosis")
    meds = body.get("availableMedications") or []
    if not diagnosis or not isinstance(meds, list):
        return jsonify({"error": "diagnosis and availableMedications are required"}), 400

    raw = generate(
        "Recommend medications ONLY from the provided list. Return JSON array: [{'name':'...','dosageNote':'...'}]. Raw JSON only, no markdown.",
        json.dumps({"diagnosis": diagnosis, "availableMedications": meds}),
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse LLM JSON", "raw": raw}), 500
    return jsonify({"recommendations": parsed})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5021)