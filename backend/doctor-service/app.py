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
SHIFT_WINDOWS = {
    "day":   {"start_hour": 7,  "duration_hours": 12},
    "night": {"start_hour": 19, "duration_hours": 12},
    "off":   None,
}
SLOTS_PER_SHIFT = 24   # 24 × 30 min = 12 hours
DAYS_AHEAD = 7

# Default shift pattern used when auto-populating a new doctor's schedule.
# Keyed by Python weekday(): 0=Monday … 6=Sunday.
WEEKLY_DEFAULT_SHIFTS = {
    0: "day",    # Monday
    1: "night",  # Tuesday
    2: "day",    # Wednesday
    3: "night",  # Thursday
    4: "day",    # Friday
    5: "night",  # Saturday
    6: "day",    # Sunday
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
    for key in ["createdAt", "updatedAt", "shiftUpdatedAt"]:
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


def get_day_ref(doctor_id, date_str):
    return (
        db.collection(DOCTOR_SCHEDULE_COLLECTION)
        .document(doctor_id)
        .collection("days")
        .document(date_str)
    )


def make_schedule_array(shift, existing=None):
    """Build a 24-element schedule array. Preserves 'booked' slots from existing."""
    if shift == "off":
        arr = ["unavailable"] * SLOTS_PER_SHIFT
    else:
        arr = ["available"] * SLOTS_PER_SHIFT
    if existing:
        for i, val in enumerate(existing[:SLOTS_PER_SHIFT]):
            if val == "booked":
                arr[i] = "booked"
    return arr


def slot_index_for_time(shift, target_dt):
    """Returns 0-23 index for a datetime within a shift, or None if out of range."""
    cfg = SHIFT_WINDOWS.get(shift)
    if not cfg:
        return None
    shift_base = target_dt.replace(
        hour=cfg["start_hour"], minute=0, second=0, microsecond=0
    )
    # Night shift: slots after midnight belong to the shift that started the previous evening
    if shift == "night" and target_dt.hour < 12:
        shift_base = shift_base - timedelta(days=1)
    delta_minutes = (target_dt - shift_base).total_seconds() / 60
    if delta_minutes < 0 or delta_minutes >= SLOTS_PER_SHIFT * 30:
        return None
    return int(delta_minutes // 30)


def slot_datetime_for_index(shift, date_str, index):
    """Returns (slot_start, slot_end) for a slot index on the shift's reference date."""
    cfg = SHIFT_WINDOWS.get(shift)
    if not cfg:
        return None, None
    date = datetime.strptime(date_str, "%Y-%m-%d")
    shift_start = date.replace(hour=cfg["start_hour"], minute=0, second=0, microsecond=0)
    slot_start = shift_start + timedelta(minutes=index * 30)
    slot_end = slot_start + timedelta(minutes=30)
    return slot_start, slot_end


def ensure_missing_day_documents(doctor_id, days=DAYS_AHEAD * 2):
    """Create day documents for any of the next `days` days that don't already exist.
    Existing documents are never modified — this preserves any shift the doctor has set.
    New documents default to shift='day'.
    """
    today = now_utc().replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
    parent_ref = db.collection(DOCTOR_SCHEDULE_COLLECTION).document(doctor_id)
    if not parent_ref.get().exists:
        parent_ref.set({"doctorID": doctor_id, "createdAt": now_utc(), "updatedAt": now_utc()})

    created = 0
    for i in range(days):
        date_str = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        day_ref = get_day_ref(doctor_id, date_str)
        if not day_ref.get().exists:
            day_ref.set({
                "date": date_str,
                "shift": "day",
                "schedule": make_schedule_array("day"),
                "createdAt": now_utc(),
                "updatedAt": now_utc(),
            })
            created += 1
    return created


def ensure_day_documents(doctor_id, shift=None, days=DAYS_AHEAD):
    """Create or update day documents for the next `days` days.

    If *shift* is None, each day gets its shift from WEEKLY_DEFAULT_SHIFTS
    (a mix of day/night based on weekday). If a specific shift is supplied,
    that shift is applied uniformly to all days.
    """
    today = now_utc().replace(tzinfo=None).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    parent_ref = db.collection(DOCTOR_SCHEDULE_COLLECTION).document(doctor_id)
    if not parent_ref.get().exists:
        parent_ref.set({"doctorID": doctor_id, "createdAt": now_utc(), "updatedAt": now_utc()})
    else:
        parent_ref.update({"updatedAt": now_utc()})

    for i in range(days):
        date = today + timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        day_shift = shift if shift is not None else WEEKLY_DEFAULT_SHIFTS[date.weekday()]
        day_ref = get_day_ref(doctor_id, date_str)
        snap = day_ref.get()
        if snap.exists:
            existing = snap.to_dict() or {}
            if existing.get("shift") == day_shift:
                continue
            new_schedule = make_schedule_array(day_shift, existing.get("schedule", []))
            day_ref.update({"shift": day_shift, "schedule": new_schedule, "updatedAt": now_utc()})
        else:
            day_ref.set({
                "date": date_str,
                "shift": day_shift,
                "schedule": make_schedule_array(day_shift),
                "createdAt": now_utc(),
                "updatedAt": now_utc(),
            })
    return days


def has_unavailable_slot(doctor_id, req_start, req_end):
    """Return True if any 30-min slot in [req_start, req_end) is 'unavailable'."""
    date_str = req_start.strftime("%Y-%m-%d")
    snap = get_day_ref(doctor_id, date_str).get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    shift = data.get("shift")
    schedule = data.get("schedule", [])
    if not shift or shift == "off":
        return True
    cursor = req_start
    while cursor < req_end:
        idx = slot_index_for_time(shift, cursor)
        if idx is not None and 0 <= idx < len(schedule):
            if schedule[idx] == "unavailable":
                return True
        cursor += timedelta(minutes=30)
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
        ensure_day_documents(payload["doctorID"])  # auto-assign mixed day/night shifts
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


@app.route("/doctor/<doctor_id>", methods=["PUT"])
def update_doctor(doctor_id):
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503
    body = request.get_json(silent=True) or {}
    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404
    updates = {key: body[key] for key in ["name", "email", "phone"] if key in body}
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updatedAt"] = now_utc()
    doc.reference.update(updates)
    return jsonify(to_json(doc.reference.get().to_dict()))


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
    return jsonify(to_json(doc.reference.get().to_dict()))


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
    updates = {"shift": shift, "shiftUpdatedAt": now_utc(), "updatedAt": now_utc()}
    if shift == "off":
        updates["status"] = "busy"
    elif current.get("status") == "busy" and str(current.get("shift") or "").lower() == "off":
        updates["status"] = "available"
    doc.reference.update(updates)
    ensure_day_documents(doctor_id, shift)
    return jsonify(to_json(doc.reference.get().to_dict()))


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
    return jsonify({"doctors": [to_json(doc.to_dict()) for doc in query.stream()]})


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


@app.route("/doctor-schedule/ensure", methods=["POST"])
def ensure_schedule():
    """Called by the frontend on login to guarantee the next 7 days exist.
    Only creates missing day documents (shift defaults to 'day').
    Never overwrites documents that already have a doctor-set shift.
    """
    if not db:
        return jsonify({"error": "Firestore is not initialised"}), 503
    body = request.get_json(silent=True) or {}
    doctor_id = body.get("doctorID")
    if not doctor_id:
        return jsonify({"error": "doctorID is required"}), 400
    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404
    created = ensure_missing_day_documents(doctor_id)
    return jsonify({"doctorID": doctor_id, "daysCreated": created})


@app.route("/doctor-schedule")
def list_doctor_schedule():
    """Return day documents for a doctor.
    Response: { doctorID, days: [{date, shift, schedule: [24 values]}] }"""
    doctor_id = request.args.get("doctorID")
    if not doctor_id:
        return jsonify({"error": "doctorID is required"}), 400

    from_str = (request.args.get("from") or "")[:10]   # YYYY-MM-DD prefix
    to_str = (request.args.get("to") or "")[:10]

    days_ref = (
        db.collection(DOCTOR_SCHEDULE_COLLECTION)
        .document(doctor_id)
        .collection("days")
    )
    days = []
    for doc in days_ref.stream():
        data = doc.to_dict() or {}
        date = data.get("date", doc.id)
        if from_str and date < from_str:
            continue
        if to_str and date > to_str:
            continue
        days.append({
            "date": date,
            "shift": data.get("shift", "off"),
            "schedule": data.get("schedule", []),
        })

    days.sort(key=lambda x: x["date"])
    return jsonify({"doctorID": doctor_id, "days": days})


@app.route("/doctor-schedule/available")
def get_schedule_available_doctors():
    """Find doctors with an 'available' slot covering the requested 30-min window."""
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

    date_str = req_start.strftime("%Y-%m-%d")

    doctors = []
    for doc in query.stream():
        doctor = doc.to_dict() or {}
        if not include_busy and doctor.get("status") != "available":
            continue
        if str(doctor.get("shift") or "").lower() == "off":
            continue

        doctor_id = doctor.get("doctorID")
        snap = get_day_ref(doctor_id, date_str).get()
        if not snap.exists:
            continue

        data = snap.to_dict() or {}
        shift = data.get("shift")
        schedule = data.get("schedule", [])
        if not shift or shift == "off":
            continue

        # Verify every requested 30-min slot is "available"
        matching_slots = []
        cursor = req_start
        all_available = True
        while cursor < req_end:
            idx = slot_index_for_time(shift, cursor)
            if idx is None or idx >= len(schedule) or schedule[idx] != "available":
                all_available = False
                break
            slot_start, slot_end = slot_datetime_for_index(shift, date_str, idx)
            matching_slots.append({
                "slotID": f"{date_str}_{idx}",
                "slotStart": slot_start.isoformat(),
                "slotEnd": slot_end.isoformat(),
            })
            cursor += timedelta(minutes=30)

        if all_available and matching_slots:
            row = to_json(doctor)
            row["matchingSlots"] = matching_slots
            doctors.append(row)

    return jsonify({
        "requestedSlot": {"slotStart": req_start.isoformat(), "slotEnd": req_end.isoformat()},
        "doctors": doctors,
        "count": len(doctors),
    })


@app.route("/doctor-schedule/doctor/<doctor_id>/alternatives")
def doctor_schedule_alternatives(doctor_id):
    """Return available 30-min slots for a doctor, sorted ascending by time."""
    limit = request.args.get("limit", default=5, type=int)
    from_dt = parse_datetime(request.args.get("from")) or now_utc().replace(tzinfo=None)
    exclude_start = parse_datetime(request.args.get("excludeStart"))
    exclude_end = parse_datetime(request.args.get("excludeEnd"))

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404

    days_ref = (
        db.collection(DOCTOR_SCHEDULE_COLLECTION)
        .document(doctor_id)
        .collection("days")
    )
    slots = []
    for day_doc in days_ref.stream():
        data = day_doc.to_dict() or {}
        date_str = data.get("date", day_doc.id)
        shift = data.get("shift")
        schedule = data.get("schedule", [])
        if not shift or shift == "off":
            continue
        for idx, status in enumerate(schedule):
            if status != "available":
                continue
            slot_start, slot_end = slot_datetime_for_index(shift, date_str, idx)
            if not slot_start or slot_end <= from_dt:
                continue
            if exclude_start and exclude_end and ranges_overlap(slot_start, slot_end, exclude_start, exclude_end):
                continue
            slots.append({
                "slotID": f"{date_str}_{idx}",
                "slotStart": slot_start.isoformat(),
                "slotEnd": slot_end.isoformat(),
            })

    slots.sort(key=lambda x: x["slotStart"])
    return jsonify({"doctorID": doctor_id, "slots": slots[:max(1, min(limit, 20))]})


@app.route("/doctor-schedule/slot", methods=["PUT"])
def update_schedule_slot():
    """Mark a specific 30-min slot as 'booked' or 'available'. Called by booking services."""
    body = request.get_json(silent=True) or {}
    doctor_id = body.get("doctorID")
    slot_start = parse_datetime(body.get("slotStart"))
    status = body.get("status")

    if not doctor_id or not slot_start or status not in ("booked", "available"):
        return jsonify({"error": "doctorID, slotStart, and status (booked/available) are required"}), 400

    date_str = slot_start.strftime("%Y-%m-%d")
    snap = get_day_ref(doctor_id, date_str).get()
    if not snap.exists:
        return jsonify({"error": "No schedule found for this day"}), 404

    data = snap.to_dict() or {}
    shift = data.get("shift")
    schedule = list(data.get("schedule", []))

    idx = slot_index_for_time(shift, slot_start)
    if idx is None or idx >= len(schedule):
        return jsonify({"error": "Slot not found in schedule"}), 404

    schedule[idx] = status
    get_day_ref(doctor_id, date_str).update({"schedule": schedule, "updatedAt": now_utc()})
    return jsonify({"doctorID": doctor_id, "date": date_str, "slotIndex": idx, "status": status})


@app.route("/doctor-calendar-event", methods=["POST"])
def create_doctor_calendar_event():
    """Block out a time range by setting affected slots to 'unavailable'."""
    body = request.get_json(silent=True) or {}
    doctor_id = body.get("doctorID")
    event_start = parse_datetime(body.get("eventStart"))
    event_end = parse_datetime(body.get("eventEnd"))
    reason = (body.get("reason") or "Unavailable").strip()

    if not doctor_id or not event_start or not event_end:
        return jsonify({"error": "doctorID, eventStart, eventEnd are required in ISO format"}), 400
    if event_end <= event_start:
        return jsonify({"error": "eventEnd must be after eventStart"}), 400

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404

    date_str = event_start.strftime("%Y-%m-%d")
    snap = get_day_ref(doctor_id, date_str).get()
    if not snap.exists:
        return jsonify({"error": "No schedule found for this day. Set a shift first."}), 404

    data = snap.to_dict() or {}
    shift = data.get("shift")
    schedule = list(data.get("schedule", []))

    if not shift or shift == "off":
        return jsonify({"error": "Doctor is off on this day"}), 400

    # Check for booked slots in the range first
    cursor = event_start
    indices = []
    while cursor < event_end:
        idx = slot_index_for_time(shift, cursor)
        if idx is not None and 0 <= idx < len(schedule):
            if schedule[idx] == "booked":
                return jsonify({"error": f"Slot at {cursor.strftime('%H:%M')} is already booked"}), 409
            indices.append(idx)
        cursor += timedelta(minutes=30)

    if not indices:
        return jsonify({"error": "No valid slots found in the specified time range"}), 400

    for idx in indices:
        schedule[idx] = "unavailable"
    get_day_ref(doctor_id, date_str).update({"schedule": schedule, "updatedAt": now_utc()})

    return jsonify({
        "doctorID": doctor_id,
        "date": date_str,
        "eventStart": event_start.isoformat(),
        "eventEnd": event_end.isoformat(),
        "reason": reason,
        "blockedSlots": len(indices),
    }), 201


@app.route("/doctor-calendar-event")
def list_doctor_calendar_events():
    """Return blocked time ranges derived from 'unavailable' slots in the schedule array."""
    doctor_id = request.args.get("doctorID")
    if not doctor_id:
        return jsonify({"error": "doctorID is required"}), 400

    from_str = (request.args.get("from") or "")[:10]
    to_str = (request.args.get("to") or "")[:10]

    days_ref = (
        db.collection(DOCTOR_SCHEDULE_COLLECTION)
        .document(doctor_id)
        .collection("days")
    )
    events = []
    for day_doc in days_ref.stream():
        data = day_doc.to_dict() or {}
        date_str = data.get("date", day_doc.id)
        shift = data.get("shift")
        schedule = data.get("schedule", [])

        if not shift or shift == "off":
            continue
        if from_str and date_str < from_str:
            continue
        if to_str and date_str > to_str:
            continue

        # Merge contiguous "unavailable" slots into event ranges
        i = 0
        while i < len(schedule):
            if schedule[i] == "unavailable":
                start_idx = i
                while i < len(schedule) and schedule[i] == "unavailable":
                    i += 1
                event_start, _ = slot_datetime_for_index(shift, date_str, start_idx)
                _, event_end = slot_datetime_for_index(shift, date_str, i - 1)
                if event_start and event_end:
                    events.append({
                        "doctorID": doctor_id,
                        "eventStart": event_start.isoformat(),
                        "eventEnd": event_end.isoformat(),
                        "reason": "Unavailable",
                    })
            else:
                i += 1

    events.sort(key=lambda e: e["eventStart"])
    return jsonify({"events": events})


@app.route("/doctor/<doctor_id>/shift-override", methods=["PUT"])
def set_shift_override(doctor_id):
    """Change the shift for a single day. Updates the day document's shift and resets schedule."""
    body = request.get_json(silent=True) or {}
    date_str = body.get("date")
    shift = normalise_shift(body.get("shift"))

    if not date_str or shift is None:
        return jsonify({"error": "date (YYYY-MM-DD) and shift (day/night/off) are required"}), 400
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format, use YYYY-MM-DD"}), 400

    doc = get_doctor_doc(doctor_id)
    if not doc:
        return jsonify({"error": "Doctor not found"}), 404

    parent_ref = db.collection(DOCTOR_SCHEDULE_COLLECTION).document(doctor_id)
    if not parent_ref.get().exists:
        parent_ref.set({"doctorID": doctor_id, "createdAt": now_utc(), "updatedAt": now_utc()})

    day_ref = get_day_ref(doctor_id, date_str)
    snap = day_ref.get()
    if snap.exists:
        existing = snap.to_dict() or {}
        new_schedule = make_schedule_array(shift, existing.get("schedule", []))
        day_ref.update({"shift": shift, "schedule": new_schedule, "updatedAt": now_utc()})
    else:
        day_ref.set({
            "date": date_str,
            "shift": shift,
            "schedule": make_schedule_array(shift),
            "createdAt": now_utc(),
            "updatedAt": now_utc(),
        })

    return jsonify({"doctorID": doctor_id, "date": date_str, "shift": shift})


@app.route("/doctor-shift-overrides")
def list_shift_overrides():
    """Return the shift for each day document. Used by schedule page to populate dropdowns."""
    doctor_id = request.args.get("doctorID")
    if not doctor_id:
        return jsonify({"error": "doctorID is required"}), 400

    from_date = (request.args.get("from") or "")[:10]
    to_date = (request.args.get("to") or "")[:10]

    days_ref = (
        db.collection(DOCTOR_SCHEDULE_COLLECTION)
        .document(doctor_id)
        .collection("days")
    )
    overrides = []
    for doc in days_ref.stream():
        data = doc.to_dict() or {}
        date = data.get("date", doc.id)
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        overrides.append({"date": date, "shift": data.get("shift", "off")})

    return jsonify({"overrides": overrides})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5031)
