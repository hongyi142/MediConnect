from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

ALLOWED_STATUS = {"pending", "confirmed", "cancelled"}
ACTIVE_STATUS = {"pending", "confirmed"}
DEFAULT_SLOT_MINUTES = 30


def parse_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def normalise_slot_from_body(body):
    slot_start = parse_datetime(body.get("slotStart"))
    slot_end = parse_datetime(body.get("slotEnd"))
    date_time = parse_datetime(body.get("dateTime"))

    if not slot_start and date_time:
        slot_start = date_time
    if not slot_end and slot_start:
        slot_end = slot_start + timedelta(minutes=DEFAULT_SLOT_MINUTES)

    if not slot_start or not slot_end:
        return None, None
    if slot_end <= slot_start:
        return None, None
    return slot_start, slot_end


def appointment_interval(appointment):
    start = parse_datetime(appointment.get("slotStart") or appointment.get("dateTime"))
    end = parse_datetime(appointment.get("slotEnd"))
    if not end and start:
        end = start + timedelta(minutes=DEFAULT_SLOT_MINUTES)
    return start, end


def to_json(data):
    out = dict(data)
    for key in [
        "dateTime",
        "slotStart",
        "slotEnd",
        "createdAt",
        "updatedAt",
        "confirmedAt",
        "cancelledAt",
    ]:
        if out.get(key) and hasattr(out[key], "isoformat"):
            dt = out[key]
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
            out[key] = dt.isoformat()
    return out


def get_appointment_doc(appointment_id):
    docs = db.collection("Appointment").where("appointmentID", "==", appointment_id).limit(1).stream()
    for doc in docs:
        return doc
    return None


@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "appointment-service"})


@app.route("/appointment/upcoming")
def get_upcoming_appointments():
    """Return confirmed (or filtered) appointments within a datetime window.
    Used by reminder-service to find appointments needing reminders.
    Query params: status (default 'confirmed'), from (ISO datetime), to (ISO datetime).
    """
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    status_filter = request.args.get("status", "confirmed")
    from_dt = parse_datetime(request.args.get("from"))
    to_dt = parse_datetime(request.args.get("to"))

    query = db.collection("Appointment")
    if status_filter:
        query = query.where("status", "==", status_filter)

    appointments = []
    for doc in query.stream():
        item = doc.to_dict() or {}
        slot_start, _ = appointment_interval(item)
        if not slot_start:
            continue
        if from_dt and slot_start < from_dt:
            continue
        if to_dt and slot_start > to_dt:
            continue
        appointments.append(to_json(item))

    appointments.sort(key=lambda x: x.get("slotStart") or x.get("dateTime") or "")
    return jsonify({"appointments": appointments})


@app.route("/appointment", methods=["POST"])
def create_appointment():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    patient_id = body.get("patientID")
    doctor_id = body.get("doctorID")
    status = body.get("status", "pending")
    slot_start, slot_end = normalise_slot_from_body(body)

    if not patient_id or not doctor_id or not slot_start or not slot_end:
        return jsonify({"error": "patientID, doctorID and a valid slot (slotStart/slotEnd or dateTime) are required"}), 400
    if status not in ALLOWED_STATUS:
        return jsonify({"error": "Invalid status"}), 400

    ref = db.collection("Appointment").document()
    payload = {
        "appointmentID": ref.id,
        "patientID": patient_id,
        "doctorID": doctor_id,
        "dateTime": slot_start,
        "slotStart": slot_start,
        "slotEnd": slot_end,
        "status": status,
        "specialisation": body.get("specialisation"),
        "reason": body.get("reason"),
        "doctorPreference": body.get("doctorPreference", "any"),
        "cancellationReason": None,
        "alternativeOptions": body.get("alternativeOptions"),
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

    doc = get_appointment_doc(appointment_id)
    if not doc:
        return jsonify({"error": "Appointment not found"}), 404
    return jsonify(to_json(doc.to_dict()))


@app.route("/appointment/patient/<patient_id>")
def get_patient_appointments(patient_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    docs = db.collection("Appointment").where("patientID", "==", patient_id).stream()
    appointments = [to_json(doc.to_dict()) for doc in docs]
    appointments.sort(key=lambda x: x.get("slotStart") or x.get("dateTime") or "")
    return jsonify({"appointments": appointments})


@app.route("/appointment/doctor/<doctor_id>")
def get_doctor_appointments(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    status = request.args.get("status")
    from_dt = parse_datetime(request.args.get("from"))
    to_dt = parse_datetime(request.args.get("to"))

    query = db.collection("Appointment").where("doctorID", "==", doctor_id)
    if status:
        query = query.where("status", "==", status)

    appointments = []
    for doc in query.stream():
        item = doc.to_dict() or {}
        slot_start, slot_end = appointment_interval(item)
        if not slot_start or not slot_end:
            continue
        if from_dt and slot_end < from_dt:
            continue
        if to_dt and slot_start > to_dt:
            continue
        appointments.append(to_json(item))

    appointments.sort(key=lambda x: x.get("slotStart") or x.get("dateTime") or "")
    return jsonify({"appointments": appointments})


@app.route("/appointment/<appointment_id>", methods=["PUT"])
def update_appointment(appointment_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    target = get_appointment_doc(appointment_id)
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

    if "dateTime" in body or "slotStart" in body or "slotEnd" in body:
        slot_start, slot_end = normalise_slot_from_body(body)
        if not slot_start or not slot_end:
            return jsonify({"error": "slotStart/slotEnd or dateTime must be valid ISO format"}), 400
        updates["dateTime"] = slot_start
        updates["slotStart"] = slot_start
        updates["slotEnd"] = slot_end

    if "doctorID" in body and body.get("doctorID"):
        updates["doctorID"] = body.get("doctorID")
    if "cancellationReason" in body and "cancellationReason" not in updates:
        updates["cancellationReason"] = body.get("cancellationReason")
    if "alternativeOptions" in body:
        updates["alternativeOptions"] = body.get("alternativeOptions")

    updates["updatedAt"] = datetime.utcnow()
    target.reference.update(updates)
    updated = target.reference.get().to_dict()
    return jsonify(to_json(updated))


@app.route("/appointment/conflict")
def check_conflict():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    doctor_id = request.args.get("doctorID")
    slot_start = parse_datetime(request.args.get("slotStart"))
    slot_end = parse_datetime(request.args.get("slotEnd"))
    date_time = parse_datetime(request.args.get("dateTime"))

    if not doctor_id:
        return jsonify({"error": "doctorID is required"}), 400

    if not slot_start and date_time:
        slot_start = date_time
    if not slot_end and slot_start:
        slot_end = slot_start + timedelta(minutes=DEFAULT_SLOT_MINUTES)

    if not slot_start or not slot_end:
        return jsonify({"error": "Provide a valid slotStart/slotEnd or dateTime"}), 400
    if slot_end <= slot_start:
        return jsonify({"error": "slotEnd must be after slotStart"}), 400

    docs = db.collection("Appointment").where("doctorID", "==", doctor_id).stream()
    for doc in docs:
        appointment = doc.to_dict() or {}
        if appointment.get("status") not in ACTIVE_STATUS:
            continue
        appt_start, appt_end = appointment_interval(appointment)
        if not appt_start or not appt_end:
            continue
        if ranges_overlap(slot_start, slot_end, appt_start, appt_end):
            return jsonify(
                {
                    "conflict": True,
                    "appointmentID": appointment.get("appointmentID"),
                    "slotStart": appt_start.isoformat(),
                    "slotEnd": appt_end.isoformat(),
                }
            )

    return jsonify({"conflict": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5032)
