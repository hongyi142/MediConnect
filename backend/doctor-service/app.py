import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def to_json(data):
    out = dict(data)
    if out.get("createdAt") and hasattr(out["createdAt"], "isoformat"):
        out["createdAt"] = out["createdAt"].isoformat()
    if out.get("updatedAt") and hasattr(out["updatedAt"], "isoformat"):
        out["updatedAt"] = out["updatedAt"].isoformat()
    return out

@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "doctor-service"})


@app.route("/doctor", methods=["POST"])
def create_doctor():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    required = ["name", "email", "phone", "specialisation"]
    missing = [field for field in required if not body.get(field)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    ref = db.collection("Doctor").document()
    payload = {
        "doctorID": ref.id,
        "name": body.get("name"),
        "email": body.get("email"),
        "phone": body.get("phone"),
        "specialisation": body.get("specialisation"),
        "status": body.get("status", "available"),
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    ref.set(payload)

    return jsonify(to_json(payload)), 201


@app.route("/doctor/<doctor_id>")
def get_doctor(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    docs = db.collection("Doctor").where("doctorID", "==", doctor_id).limit(1).stream()
    for doc in docs:
        return jsonify(to_json(doc.to_dict()))
    return jsonify({"error": "Doctor not found"}), 404


@app.route("/doctor/<doctor_id>/status", methods=["PUT"])
def update_doctor_status(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in ["available", "busy"]:
        return jsonify({"error": "status must be 'available' or 'busy'"}), 400

    docs = db.collection("Doctor").where("doctorID", "==", doctor_id).limit(1).stream()
    target = None
    for doc in docs:
        target = doc
        break

    if not target:
        return jsonify({"error": "Doctor not found"}), 404

    target.reference.update({"status": status, "updatedAt": datetime.utcnow()})
    updated = target.reference.get().to_dict()
    return jsonify(to_json(updated))


@app.route("/doctor/available")
def get_available_doctors():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    specialization = request.args.get("specialisation")
    query = db.collection("Doctor").where("status", "==", "available")
    if specialization:
        query = query.where("specialisation", "==", specialization)

    doctors = [to_json(doc.to_dict()) for doc in query.stream()]
    return jsonify({"doctors": doctors})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5031)
