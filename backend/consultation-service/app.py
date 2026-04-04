import json
import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from google.cloud import firestore

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


def init_firestore():
    path = os.environ.get("FIREBASE_CRED_PATH", "./firebase_credentials.json")
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    return firestore.Client(project=config["projectId"])


def to_json(data):
    out = dict(data)
    if out.get("startTime"):
        out["startTime"] = out["startTime"].isoformat()
    if out.get("endTime"):
        out["endTime"] = out["endTime"].isoformat()
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
    return jsonify({"status": "ok", "service": "consultation-service"})


@app.route("/consultation", methods=["POST"])
def create_consultation():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503
    body = request.get_json(silent=True) or {}
    appt_id = body.get("apptID")
    room_name = body.get("roomName")
    if not appt_id or not room_name:
        return jsonify({"error": "apptID and roomName are required"}), 400

    ref = db.collection("Consultation").document()
    payload = {
        "consultationID": ref.id,
        "apptID": appt_id,
        "roomName": room_name,
        "notes": "",
        "summary": "",
        "mcIssued": False,
        "mcKey": None,
        "startTime": datetime.utcnow(),
        "endTime": None,
        "status": "active",
    }
    ref.set(payload)
    return (
        jsonify(
            {
                "consultationID": ref.id,
                "apptID": appt_id,
                "roomName": room_name,
                "startTime": payload["startTime"].isoformat(),
            }
        ),
        201,
    )


def find_by_appt(appt_id):
    docs = db.collection("Consultation").where("apptID", "==", appt_id).limit(1).stream()
    for doc in docs:
        return doc
    return None


@app.route("/consultation/<appt_id>")
def get_consultation(appt_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503
    doc = find_by_appt(appt_id)
    if not doc:
        return jsonify({"error": "Consultation not found"}), 404
    return jsonify(to_json(doc.to_dict()))


@app.route("/consultation/<appt_id>/notes", methods=["PUT"])
def update_notes(appt_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503
    body = request.get_json(silent=True) or {}
    notes = body.get("notes")
    if notes is None:
        return jsonify({"error": "notes is required"}), 400
    doc = find_by_appt(appt_id)
    if not doc:
        return jsonify({"error": "Consultation not found"}), 404
    doc.reference.update({"notes": notes})
    return jsonify({"message": "Notes updated", "apptID": appt_id})


@app.route("/consultation/<appt_id>/complete", methods=["PUT"])
def complete_consultation(appt_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503
    body = request.get_json(silent=True) or {}
    doc = find_by_appt(appt_id)
    if not doc:
        return jsonify({"error": "Consultation not found"}), 404

    updates = {
        "summary": body.get("summary", ""),
        "mcIssued": bool(body.get("mcIssued", False)),
        "mcKey": body.get("mcKey"),
        "medications": body.get("medications", []),
        "endTime": datetime.utcnow(),
        "status": "completed",
    }
    if body.get("patientID"):
        updates["patientID"] = body["patientID"]
    if body.get("doctorID"):
        updates["doctorID"] = body["doctorID"]
    doc.reference.update(updates)
    updated = doc.reference.get().to_dict()
    return jsonify(to_json(updated))


@app.route("/consultation/patient/<patient_id>")
def get_consultations_by_patient(patient_id):
    """Return all completed consultations for a patient, newest first."""
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    # We join via the appointment-service by matching apptIDs that belong to this patient.
    # Instead, store patientID on the consultation at creation time (added below).
    # For existing records we fall back to filtering by apptID prefix is not viable,
    # so we accept patientID as a denormalised field written at complete time.
    docs = (
        db.collection("Consultation")
        .where("patientID", "==", patient_id)
        .stream()
    )
    results = sorted(
        [to_json(d.to_dict()) for d in docs],
        key=lambda c: c.get("endTime") or c.get("startTime") or "",
        reverse=True,
    )
    return jsonify({"consultations": results})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004)