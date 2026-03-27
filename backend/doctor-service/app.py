from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import auth, credentials, firestore

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

DEFAULT_STAFF_PASSWORD = "NewStaff123!"
DOCTOR_COLLECTION = "Doctor"
DOCTOR_SCHEDULE_COLLECTION = "DoctorSchedule"
DOCTOR_BLOCK_COLLECTION = "DoctorCalendarEvent"
SHIFT_WINDOWS = {
    "day": {"start_hour": 7, "duration_hours": 12},
    "night": {"start_hour": 19, "duration_hours": 12},
    "off": None,
}


def now_utc():
    return datetime.now(timezone.utc)


def parse_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(v)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def to_json(data):
    out = dict(data)
    for key in [
        "createdAt",
        "updatedAt",
        "slotStart",
        "slotEnd",
        "shiftUpdatedAt",
        "eventStart",
        "eventEnd",
    ]:
        if out.get(key) and hasattr(out[key], "isoformat"):
            dt = out[key]
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
            out[key] = dt.isoformat()
    return out


def ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def get_doctor_doc(doctor_id):
    docs = db.collection(DOCTOR_COLLECTION).where("doctorID", "==", doctor_id).limit(1).stream()
    for doc in docs:
        return doc
    return None


def next_schedule_slot_id():
    """
    Generate a numeric string slotID compatible with environments where
    firebase_admin.firestore does not expose transaction helpers.
    """
    max_id = 0
    docs = db.collection(DOCTOR_SCHEDULE_COLLECTION).stream()
    for doc in docs:
        row = doc.to_dict() or {}
        raw = str(row.get("slotID") or "").strip()
        if raw.isdigit():
            max_id = max(max_id, int(raw))
    return str(max_id + 1)


def normalise_shift(value):
    shift = str(value or "").strip().lower()
    if shift in SHIFT_WINDOWS:
        return shift
    return None


def shift_bounds_for_date(shift, dt):
    cfg = SHIFT_WINDOWS.get(shift)
    if not cfg:
        return None, None
    start = dt.replace(hour=cfg["start_hour"], minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=cfg["duration_hours"])
    return start, end


def ensure_shift_schedule_slots(doctor_id, shift, days=14):
    if shift not in ("day", "night"):
        return 0

    window_start = now_utc().replace(tzinfo=None)
    window_end = window_start + timedelta(days=days + 1)

    existing_keys = set()
    query = (
        db.collection(DOCTOR_SCHEDULE_COLLECTION)
        .where("doctorID", "==", doctor_id)
        .stream()
    )
    for doc in query:
        row = doc.to_dict() or {}
        start = parse_datetime(row.get("slotStart"))
        end = parse_datetime(row.get("slotEnd"))
        if not start or not end:
            continue
        if end < window_start or start > window_end:
            continue
        existing_keys.add(f"{start.isoformat()}|{end.isoformat()}")

    created = 0
    cursor = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = days
    if shift == "night":
        cursor = cursor - timedelta(days=1)
        total_days = days + 1

    for day_offset in range(total_days):
        current_day = cursor + timedelta(days=day_offset)
        slot_start, slot_end = shift_bounds_for_date(shift, current_day)
        if not slot_start or not slot_end:
            continue
        key = f"{slot_start.isoformat()}|{slot_end.isoformat()}"
        if key in existing_keys:
            continue

        ref = db.collection(DOCTOR_SCHEDULE_COLLECTION).document()
        payload = {
            "slotID": next_schedule_slot_id(),
            "doctorID": doctor_id,
            "slotStart": slot_start,
            "slotEnd": slot_end,
            "createdAt": now_utc(),
            "updatedAt": now_utc(),
            "source": "shift",
            "shiftType": shift,
        }
        ref.set(payload)
        created += 1
        existing_keys.add(key)
    return created


def has_blocking_event(doctor_id, req_start, req_end):
    docs = db.collection(DOCTOR_BLOCK_COLLECTION).where("doctorID", "==", doctor_id).stream()
    for doc in docs:
        row = doc.to_dict() or {}
        event_start = parse_datetime(row.get("eventStart"))
        event_end = parse_datetime(row.get("eventEnd"))
        if not event_start or not event_end:
            continue
        if ranges_overlap(req_start, req_end, event_start, event_end):
            return True
    return False


def create_staff_user_doc(firebase_uid, role, name, linked_id, email):
    ref = db.collection("users").document(firebase_uid)
    snap = ref.get()
    payload = {
        "uid": firebase_uid,
        "role": role,
        "name": name,
        "linkedID": linked_id,
        "email": email,
        "updatedAt": now_utc(),
    }
    if not snap.exists:
        payload["createdAt"] = now_utc()
    ref.set(payload, merge=True)


def maybe_create_auth_user(body, name, email):
    create_auth = body.get("createAuth", True)
    if create_auth is False:
        return body.get("firebaseUID"), False
    user = auth.create_user(
        email=email,
        password=DEFAULT_STAFF_PASSWORD,
        display_name=name,
        disabled=False,
    )
    return user.uid, True


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

    shift = normalise_shift(body.get("shift", "day")) or "day"
    default_status = body.get("status")
    if not default_status:
        default_status = "busy" if shift == "off" else "available"

    firebase_uid = None
    auth_created = False
    try:
        firebase_uid, auth_created = maybe_create_auth_user(body, body["name"], body["email"])

        ref = db.collection(DOCTOR_COLLECTION).document()
        payload = {
            "doctorID": ref.id,
            "name": body.get("name"),
            "email": body.get("email"),
            "phone": body.get("phone"),
            "specialisation": body.get("specialisation"),
            "status": default_status,
            "shift": shift,
            "shiftUpdatedAt": now_utc(),
            "firebaseUID": firebase_uid,
            "createdAt": now_utc(),
            "updatedAt": now_utc(),
        }
        ref.set(payload)
        if shift in ("day", "night"):
            ensure_shift_schedule_slots(payload["doctorID"], shift)

        if firebase_uid:
            create_staff_user_doc(
                firebase_uid=firebase_uid,
                role="doctor",
                name=payload["name"],
                linked_id=payload["doctorID"],
                email=payload["email"],
            )

        response = to_json(payload)
        if auth_created:
            response["defaultPassword"] = DEFAULT_STAFF_PASSWORD
        return jsonify(response), 201
    except Exception:
        if auth_created and firebase_uid:
            try:
                auth.delete_user(firebase_uid)
            except Exception:
                pass
        raise


@app.route("/doctor/<doctor_id>")
def get_doctor(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404
    return jsonify(to_json(doc.to_dict()))


@app.route("/doctor/<doctor_id>/status", methods=["PUT"])
def update_doctor_status(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in ["available", "busy"]:
        return jsonify({"error": "status must be 'available' or 'busy'"}), 400

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404

    doc.reference.update({"status": status, "updatedAt": now_utc()})
    updated = doc.reference.get().to_dict()
    return jsonify(to_json(updated))


@app.route("/doctor/<doctor_id>/shift", methods=["PUT"])
def update_doctor_shift(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    body = request.get_json(silent=True) or {}
    shift = normalise_shift(body.get("shift"))
    if not shift:
        return jsonify({"error": "shift must be one of: day, night, off"}), 400

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404

    current = doc.to_dict() or {}
    updates = {
        "shift": shift,
        "shiftUpdatedAt": now_utc(),
        "updatedAt": now_utc(),
    }
    if shift == "off":
        updates["status"] = "busy"
    elif current.get("status") == "busy" and str(current.get("shift") or "").lower() == "off":
        updates["status"] = "available"

    doc.reference.update(updates)
    generated = ensure_shift_schedule_slots(doctor_id, shift, days=14)
    updated = doc.reference.get().to_dict() or {}
    response = to_json(updated)
    response["generatedShiftSlots"] = generated
    return jsonify(response)


@app.route("/doctor/list")
def list_doctors():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    specialization = request.args.get("specialisation")
    status = request.args.get("status")
    shift = normalise_shift(request.args.get("shift"))
    query = db.collection(DOCTOR_COLLECTION)
    if specialization:
        query = query.where("specialisation", "==", specialization)
    if status:
        query = query.where("status", "==", status)
    if shift:
        query = query.where("shift", "==", shift)

    doctors = [to_json(doc.to_dict()) for doc in query.stream()]
    return jsonify({"doctors": doctors})


@app.route("/doctor/available")
def get_available_doctors():
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503

    specialization = request.args.get("specialisation")
    query = db.collection(DOCTOR_COLLECTION).where("status", "==", "available")
    if specialization:
        query = query.where("specialisation", "==", specialization)

    doctors = [to_json(doc.to_dict()) for doc in query.stream()]
    doctors = [d for d in doctors if str(d.get("shift") or "").lower() != "off"]
    return jsonify({"doctors": doctors})


@app.route("/doctor-schedule", methods=["POST"])
def create_doctor_schedule():
    body = request.get_json(silent=True) or {}
    doctor_id = body.get("doctorID")
    slot_start = parse_datetime(body.get("slotStart"))
    slot_end = parse_datetime(body.get("slotEnd"))

    if not doctor_id or not slot_start or not slot_end:
        return jsonify({"error": "doctorID, slotStart, slotEnd are required in ISO format"}), 400
    if slot_end <= slot_start:
        return jsonify({"error": "slotEnd must be after slotStart"}), 400

    doctor_doc = get_doctor_doc(doctor_id)
    if not doctor_doc:
        return jsonify({"error": "Doctor not found"}), 404

    existing = db.collection(DOCTOR_SCHEDULE_COLLECTION).where("doctorID", "==", doctor_id).stream()
    for item in existing:
        row = item.to_dict() or {}
        existing_start = parse_datetime(row.get("slotStart"))
        existing_end = parse_datetime(row.get("slotEnd"))
        if existing_start and existing_end and ranges_overlap(slot_start, slot_end, existing_start, existing_end):
            return jsonify({"error": "Schedule overlaps with an existing slot", "slotID": row.get("slotID")}), 409

    slot_id = next_schedule_slot_id()
    ref = db.collection(DOCTOR_SCHEDULE_COLLECTION).document()
    payload = {
        "slotID": slot_id,
        "doctorID": doctor_id,
        "slotStart": slot_start,
        "slotEnd": slot_end,
        "createdAt": now_utc(),
        "updatedAt": now_utc(),
    }
    ref.set(payload)
    return jsonify(to_json(payload)), 201


@app.route("/doctor-schedule")
def list_doctor_schedule():
    doctor_id = request.args.get("doctorID")
    from_dt = parse_datetime(request.args.get("from"))
    to_dt = parse_datetime(request.args.get("to"))
    req_start = parse_datetime(request.args.get("slotStart"))
    req_end = parse_datetime(request.args.get("slotEnd"))
    contains = str(request.args.get("contains", "false")).lower() == "true"
    include_doctor = str(request.args.get("includeDoctor", "false")).lower() == "true"

    query = db.collection(DOCTOR_SCHEDULE_COLLECTION)
    if doctor_id:
        query = query.where("doctorID", "==", doctor_id)

    slots = []
    for doc in query.stream():
        slot = doc.to_dict() or {}
        slot_start = parse_datetime(slot.get("slotStart"))
        slot_end = parse_datetime(slot.get("slotEnd"))
        if not slot_start or not slot_end:
            continue

        if from_dt and slot_end < from_dt:
            continue
        if to_dt and slot_start > to_dt:
            continue
        if req_start and req_end:
            if contains and not (slot_start <= req_start and slot_end >= req_end):
                continue
            if not contains and not ranges_overlap(slot_start, slot_end, req_start, req_end):
                continue

        row = to_json(slot)
        if include_doctor:
            doctor_doc = get_doctor_doc(slot.get("doctorID"))
            if doctor_doc:
                row["doctor"] = to_json(doctor_doc.to_dict())
        slots.append(row)

    slots.sort(key=lambda x: x.get("slotStart", ""))
    return jsonify({"slots": slots})


@app.route("/doctor-schedule/<slot_id>")
def get_doctor_schedule_slot(slot_id):
    docs = db.collection(DOCTOR_SCHEDULE_COLLECTION).where("slotID", "==", slot_id).limit(1).stream()
    for doc in docs:
        return jsonify(to_json(doc.to_dict()))
    return jsonify({"error": "Schedule slot not found"}), 404


@app.route("/doctor-calendar-event", methods=["POST"])
def create_doctor_calendar_event():
    body = request.get_json(silent=True) or {}
    doctor_id = body.get("doctorID")
    event_start = parse_datetime(body.get("eventStart"))
    event_end = parse_datetime(body.get("eventEnd"))
    reason = (body.get("reason") or "").strip()

    if not doctor_id or not event_start or not event_end:
        return jsonify({"error": "doctorID, eventStart, eventEnd are required in ISO format"}), 400
    if event_end <= event_start:
        return jsonify({"error": "eventEnd must be after eventStart"}), 400

    doctor_doc = get_doctor_doc(doctor_id)
    if not doctor_doc:
        return jsonify({"error": "Doctor not found"}), 404

    query = db.collection(DOCTOR_BLOCK_COLLECTION).where("doctorID", "==", doctor_id).stream()
    for doc in query:
        row = doc.to_dict() or {}
        old_start = parse_datetime(row.get("eventStart"))
        old_end = parse_datetime(row.get("eventEnd"))
        if old_start and old_end and ranges_overlap(event_start, event_end, old_start, old_end):
            return jsonify({"error": "Calendar event overlaps with an existing event"}), 409

    ref = db.collection(DOCTOR_BLOCK_COLLECTION).document()
    payload = {
        "eventID": ref.id,
        "doctorID": doctor_id,
        "eventStart": event_start,
        "eventEnd": event_end,
        "reason": reason or "Unavailable",
        "createdAt": now_utc(),
        "updatedAt": now_utc(),
    }
    ref.set(payload)
    return jsonify(to_json(payload)), 201


@app.route("/doctor-calendar-event")
def list_doctor_calendar_events():
    doctor_id = request.args.get("doctorID")
    if not doctor_id:
        return jsonify({"error": "doctorID is required"}), 400

    from_dt = parse_datetime(request.args.get("from"))
    to_dt = parse_datetime(request.args.get("to"))
    query = db.collection(DOCTOR_BLOCK_COLLECTION).where("doctorID", "==", doctor_id)

    events = []
    for doc in query.stream():
        row = doc.to_dict() or {}
        event_start = parse_datetime(row.get("eventStart"))
        event_end = parse_datetime(row.get("eventEnd"))
        if not event_start or not event_end:
            continue
        if from_dt and event_end < from_dt:
            continue
        if to_dt and event_start > to_dt:
            continue
        events.append(to_json(row))

    events.sort(key=lambda e: e.get("eventStart", ""))
    return jsonify({"events": events})


@app.route("/doctor-schedule/available")
def get_schedule_available_doctors():
    req_start = parse_datetime(request.args.get("slotStart"))
    req_end = parse_datetime(request.args.get("slotEnd"))
    if not req_start or not req_end:
        return jsonify({"error": "slotStart and slotEnd are required"}), 400
    if req_end <= req_start:
        return jsonify({"error": "slotEnd must be after slotStart"}), 400

    preferred_doctor_id = request.args.get("doctorID")
    specialization = request.args.get("specialisation")
    include_busy = str(request.args.get("includeBusy", "false")).lower() == "true"

    query = db.collection(DOCTOR_COLLECTION)
    if preferred_doctor_id:
        query = query.where("doctorID", "==", preferred_doctor_id)
    if specialization:
        query = query.where("specialisation", "==", specialization)

    doctors = []
    for doc in query.stream():
        doctor = doc.to_dict() or {}
        if not include_busy and doctor.get("status") != "available":
            continue
        if str(doctor.get("shift") or "").lower() == "off":
            continue
        if has_blocking_event(doctor.get("doctorID"), req_start, req_end):
            continue

        matches = []
        schedules = db.collection(DOCTOR_SCHEDULE_COLLECTION).where("doctorID", "==", doctor.get("doctorID")).stream()
        for schedule_doc in schedules:
            slot = schedule_doc.to_dict() or {}
            slot_start = parse_datetime(slot.get("slotStart"))
            slot_end = parse_datetime(slot.get("slotEnd"))
            if slot_start and slot_end and slot_start <= req_start and slot_end >= req_end:
                matches.append(
                    {
                        "slotID": slot.get("slotID"),
                        "slotStart": slot_start.isoformat(),
                        "slotEnd": slot_end.isoformat(),
                    }
                )

        if matches:
            row = to_json(doctor)
            row["matchingSlots"] = matches
            doctors.append(row)

    return jsonify(
        {
            "requestedSlot": {"slotStart": req_start.isoformat(), "slotEnd": req_end.isoformat()},
            "doctors": doctors,
            "count": len(doctors),
        }
    )


@app.route("/doctor-schedule/doctor/<doctor_id>/alternatives")
def doctor_schedule_alternatives(doctor_id):
    limit = request.args.get("limit", default=5, type=int)
    from_dt = parse_datetime(request.args.get("from")) or now_utc()
    exclude_start = parse_datetime(request.args.get("excludeStart"))
    exclude_end = parse_datetime(request.args.get("excludeEnd"))

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404

    slots = []
    schedules = db.collection(DOCTOR_SCHEDULE_COLLECTION).where("doctorID", "==", doctor_id).stream()
    for schedule_doc in schedules:
        row = schedule_doc.to_dict() or {}
        slot_start = parse_datetime(row.get("slotStart"))
        slot_end = parse_datetime(row.get("slotEnd"))
        if not slot_start or not slot_end:
            continue
        if slot_end < from_dt:
            continue
        if exclude_start and exclude_end and ranges_overlap(slot_start, slot_end, exclude_start, exclude_end):
            continue
        slots.append(to_json(row))

    slots.sort(key=lambda x: x.get("slotStart", ""))
    return jsonify({"doctorID": doctor_id, "slots": slots[: max(1, min(limit, 20))]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5031)
