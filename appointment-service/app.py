import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS
from config import init_firestore

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])

ALLOWED_STATUS = {"pending", "confirmed", "cancelled"}


def parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def to_json(data):
    out = dict(data)
    for key in ["dateTime", "createdAt", "updatedAt", "confirmedAt", "cancelledAt"]:
        if out.get(key) and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
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
    return jsonify({"status": "ok", "service": "appointment-service"})


@app.route("/appointment", methods=["POST"])
def create_appointment():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    patient_id = body.get("patientID")
    doctor_id = body.get("doctorID")
    date_time_raw = body.get("dateTime")
    status = body.get("status", "pending")

    if not patient_id or not doctor_id or not date_time_raw:
        return jsonify({"error": "patientID, doctorID and dateTime are required"}), 400
    if status not in ALLOWED_STATUS:
        return jsonify({"error": "Invalid status"}), 400

    date_time = parse_datetime(date_time_raw)
    if not date_time:
        return jsonify({"error": "dateTime must be valid ISO format"}), 400

    ref = db.collection("Appointment").document()
    payload = {
        "appointmentID": ref.id,
        "patientID": patient_id,
        "doctorID": doctor_id,
        "dateTime": date_time,
        "status": status,
        "cancellationReason": None,
        "confirmedAt": None,
        "cancelledAt": None,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    ref.set(payload)
    return jsonify(to_json(payload)), 201


@app.route("/appointment/<appointment_id>")
def get_appointment(appointment_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    docs = db.collection("Appointment").where("appointmentID", "==", appointment_id).limit(1).stream()
    for doc in docs:
        return jsonify(to_json(doc.to_dict()))
    return jsonify({"error": "Appointment not found"}), 404


@app.route("/appointment/patient/<patient_id>")
def get_patient_appointments(patient_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    docs = db.collection("Appointment").where("patientID", "==", patient_id).stream()
    appointments = [to_json(doc.to_dict()) for doc in docs]
    return jsonify({"appointments": appointments})


@app.route("/appointment/<appointment_id>", methods=["PUT"])
def update_appointment(appointment_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    docs = db.collection("Appointment").where("appointmentID", "==", appointment_id).limit(1).stream()
    target = None
    for doc in docs:
        target = doc
        break
    if not target:
        return jsonify({"error": "Appointment not found"}), 404

    body = request.get_json(silent=True) or {}
    updates = {}

    if "status" in body:
        status = body.get("status")
        if status not in ALLOWED_STATUS:
            return jsonify({"error": "Invalid status"}), 400
        updates["status"] = status
        if status == "confirmed":
            updates["confirmedAt"] = datetime.utcnow()
        if status == "cancelled":
            updates["cancelledAt"] = datetime.utcnow()
            updates["cancellationReason"] = body.get("cancellationReason")

    if "dateTime" in body:
        parsed = parse_datetime(body.get("dateTime"))
        if not parsed:
            return jsonify({"error": "dateTime must be valid ISO format"}), 400
        updates["dateTime"] = parsed

    updates["updatedAt"] = datetime.utcnow()
    target.reference.update(updates)
    updated = target.reference.get().to_dict()
    return jsonify(to_json(updated))


@app.route("/appointment/conflict")
def check_conflict():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    doctor_id = request.args.get("doctorID")
    date_time_raw = request.args.get("dateTime")
    if not doctor_id or not date_time_raw:
        return jsonify({"error": "doctorID and dateTime are required"}), 400

    date_time = parse_datetime(date_time_raw)
    if not date_time:
        return jsonify({"error": "dateTime must be valid ISO format"}), 400

    docs = (
        db.collection("Appointment")
        .where("doctorID", "==", doctor_id)
        .where("dateTime", "==", date_time)
        .stream()
    )

    for doc in docs:
        appointment = doc.to_dict()
        if appointment.get("status") in ["pending", "confirmed"]:
            return jsonify({"conflict": True, "appointmentID": appointment.get("appointmentID")})

    return jsonify({"conflict": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5032)
