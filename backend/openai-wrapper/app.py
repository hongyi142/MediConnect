import json
import os
import re

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from openai import OpenAI

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])

BOOKING_SPECIALISATIONS = [
    "General Practice",
    "Dermatology",
    "Cardiology",
    "Orthopaedics",
    "ENT",
    "Ophthalmology",
    "Gastroenterology",
    "Neurology",
    "Psychiatry",
    "Gynaecology",
    "Paediatrics",
]

SPECIALISATION_SYNONYMS = {
    "general": "General Practice",
    "gp": "General Practice",
    "family medicine": "General Practice",
    "skin": "Dermatology",
    "heart": "Cardiology",
    "ortho": "Orthopaedics",
    "bone": "Orthopaedics",
    "ear nose throat": "ENT",
    "ent": "ENT",
    "eye": "Ophthalmology",
    "stomach": "Gastroenterology",
    "digestive": "Gastroenterology",
    "brain": "Neurology",
    "mental": "Psychiatry",
    "women": "Gynaecology",
    "gynae": "Gynaecology",
    "child": "Paediatrics",
    "pediatric": "Paediatrics",
}

MEDICAL_KEYWORDS = {
    "pain",
    "ache",
    "fever",
    "cough",
    "nausea",
    "vomit",
    "dizzy",
    "dizziness",
    "rash",
    "diarrhea",
    "diarrhoea",
    "swelling",
    "headache",
    "migraine",
    "breath",
    "chest",
    "throat",
    "ear",
    "eye",
    "nose",
    "fatigue",
    "weakness",
    "cramp",
    "bleeding",
    "infection",
    "allergy",
    "itch",
    "injury",
    "symptom",
    "body",
    "stomach",
    "abdomen",
}


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


def looks_like_medical_symptom(text):
    lowered = (text or "").lower()
    tokens = set(re.findall(r"[a-zA-Z]+", lowered))
    if not tokens:
        return False
    return any(keyword in lowered or keyword in tokens for keyword in MEDICAL_KEYWORDS)


def normalise_specialisation(raw):
    if not raw:
        return "General Practice"

    raw_str = str(raw).strip()
    for choice in BOOKING_SPECIALISATIONS:
        if raw_str.lower() == choice.lower():
            return choice

    raw_lower = raw_str.lower()
    for key, mapped in SPECIALISATION_SYNONYMS.items():
        if key in raw_lower:
            return mapped

    return "General Practice"


def normalise_urgency(raw):
    value = str(raw or "").strip().lower()
    if value in {"emergency", "high", "urgent"}:
        return "emergency"
    if value in {"visit", "medium", "moderate", "in_person"}:
        return "visit"
    return "teleconsult"


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

    allergies = body.get("allergies") or []
    past_history = body.get("pastHistory") or []

    patient_context = ""
    if allergies:
        patient_context += f"\nKnown allergies: {', '.join(str(a) for a in allergies)}."
    if past_history:
        patient_context += f"\nPast medical history: {', '.join(str(h) for h in past_history)}."

    system_prompt = (
        "You are a clinical assistant. Given a doctor's raw consultation notes, produce a structured summary with: "
        "Chief Complaint, Findings, Diagnosis, Treatment Plan. Be concise. Do not invent information not in the notes."
    )
    if patient_context:
        system_prompt += (
            f"\n\nPatient context (use to flag relevant interactions or considerations):{patient_context}"
        )

    summary = generate(system_prompt, notes)
    return jsonify({"summary": summary})


@app.route("/openai/symptom-check", methods=["POST"])
def symptom_check():
    body = request.get_json(silent=True) or {}
    symptoms = body.get("symptoms")
    if not symptoms:
        return jsonify({"error": "symptoms is required"}), 400
    if not looks_like_medical_symptom(symptoms):
        return jsonify(
            {
                "error": "Symptom checker accepts medical symptom descriptions only.",
                "allowedSpecialisations": BOOKING_SPECIALISATIONS,
            }
        ), 400

    allergies = body.get("allergies") or []
    past_history = body.get("pastHistory") or []

    patient_context = ""
    if allergies:
        patient_context += f"\nKnown allergies: {', '.join(str(a) for a in allergies)}."
    if past_history:
        patient_context += f"\nPast medical history: {', '.join(str(h) for h in past_history)}."

    system_prompt = (
        "You are a medical triage assistant. Accept only symptom descriptions and do not respond to unrelated requests. "
        f'Choose "specialisation" from this exact list: {", ".join(BOOKING_SPECIALISATIONS)}. '
        'Return strict raw JSON with keys: "specialisation", "urgency", "reasoning", "advice". '
        'urgency must be one of: "teleconsult", "visit", "emergency". '
        "Keep it concise and avoid definitive diagnosis claims."
    )
    if patient_context:
        system_prompt += (
            f"\n\nPatient context (factor into urgency and advice):{patient_context}"
        )

    raw = generate(system_prompt, symptoms)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse LLM response", "raw": raw}), 500

    parsed["specialisation"] = normalise_specialisation(parsed.get("specialisation"))
    parsed["urgency"] = normalise_urgency(parsed.get("urgency"))
    if not parsed.get("reasoning"):
        parsed["reasoning"] = "Based on your symptoms, a doctor should assess you to confirm the cause."
    if not parsed.get("advice"):
        parsed["advice"] = "Book a consultation for proper diagnosis and treatment."
    return jsonify(parsed)


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
