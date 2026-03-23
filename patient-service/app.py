import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS
from config import init_firestore

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


def to_json(data):
    out = dict(data)
    if out.get("birthDate") and hasattr(out["birthDate"], "isoformat"):
        out["birthDate"] = out["birthDate"].isoformat()
    if out.get("createdAt") and hasattr(out["createdAt"], "isoformat"):
        out["createdAt"] = out["createdAt"].isoformat()
    if out.get("updatedAt") and hasattr(out["updatedAt"], "isoformat"):
        out["updatedAt"] = out["updatedAt"].isoformat()
    return out


try:
    db = init_firestore()
except Exception:
    db = None


@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "patient-service"})


@app.route("/patient", methods=["POST"])
def create_patient():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    required = ["name", "email", "phone", "NRIC"]
    missing = [field for field in required if not body.get(field)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    ref = db.collection("Patient").document()
    payload = {
        "patientID": ref.id,
        "name": body.get("name"),
        "email": body.get("email"),
        "phone": body.get("phone"),
        "NRIC": body.get("NRIC"),
        "address": body.get("address", ""),
        "postalCode": body.get("postalCode"),
        "birthDate": body.get("birthDate"),
        "allergies": body.get("allergies", []),
        "pastHistory": body.get("pastHistory", []),
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    ref.set(payload)

    output = to_json(payload)
    return jsonify(output), 201


@app.route("/patient/<patient_id>")
def get_patient(patient_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    docs = db.collection("Patient").where("patientID", "==", patient_id).limit(1).stream()
    for doc in docs:
        return jsonify(to_json(doc.to_dict()))
    return jsonify({"error": "Patient not found"}), 404


@app.route("/patient/<patient_id>", methods=["PUT"])
def update_patient(patient_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    docs = db.collection("Patient").where("patientID", "==", patient_id).limit(1).stream()
    target = None
    for doc in docs:
        target = doc
        break

    if not target:
        return jsonify({"error": "Patient not found"}), 404

    updates = {
        key: body[key]
        for key in ["name", "email", "phone", "NRIC", "address", "postalCode", "birthDate", "allergies", "pastHistory"]
        if key in body
    }
    updates["updatedAt"] = datetime.utcnow()

    target.reference.update(updates)
    updated = target.reference.get().to_dict()
    return jsonify(to_json(updated))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5030)
