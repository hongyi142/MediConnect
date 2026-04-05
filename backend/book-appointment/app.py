import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pika
import redis as redis_lib
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])

DOCTOR_ROUTING_KEY = "doctor.appointment.request"
DOCTOR_NOTIFICATION_QUEUE = "doctor_appointment_frontend_queue"
SERVICE_EXCHANGE = "service_exchange"

_doctor_notifications = defaultdict(list)
_doctor_notifications_lock = threading.Lock()


def req(method, url, **kwargs):
    try:
        return requests.request(method, url, timeout=10, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(str(exc)) from exc


def parse_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def get_slot_from_payload(body):
    slot_start = parse_datetime(body.get("slotStart")) or parse_datetime(body.get("dateTime"))
    slot_end = parse_datetime(body.get("slotEnd"))
    if slot_start and not slot_end:
        slot_end = slot_start + timedelta(minutes=30)
    if not slot_start or not slot_end or slot_end <= slot_start:
        return None, None
    return slot_start, slot_end


def get_service_urls():
    patient_url = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")
    doctor_url = os.environ.get("DOCTOR_SERVICE_URL", "http://doctor-service:5031").rstrip("/")
    appointment_url = os.environ.get("APPOINTMENT_SERVICE_URL", "http://appointment-service:5032").rstrip("/")
    notification_url = os.environ.get("NOTIFICATION_WRAPPER_URL", "http://notification-wrapper:5011").rstrip("/")
    return patient_url, doctor_url, appointment_url, notification_url


_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is None:
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _redis_client = redis_lib.from_url(redis_url, decode_responses=True)
    return _redis_client


def publish_rabbit_message(routing_key, payload):
    rabbitmq_url = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    try:
        params = pika.URLParameters(rabbitmq_url)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(exchange=SERVICE_EXCHANGE, exchange_type="direct", durable=True)
        channel.basic_publish(
            exchange=SERVICE_EXCHANGE,
            routing_key=routing_key,
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE),
        )
        connection.close()
        return True, None
    except Exception as exc:
        return False, str(exc)


def store_doctor_notification(payload):
    doctor_id = payload.get("doctorID")
    if not doctor_id:
        return
    event = dict(payload)
    event["receivedAt"] = datetime.utcnow().isoformat()
    with _doctor_notifications_lock:
        _doctor_notifications[doctor_id].append(event)
        _doctor_notifications[doctor_id] = _doctor_notifications[doctor_id][-100:]


def doctor_notification_callback(ch, method, properties, body):
    try:
        msg = json.loads(body.decode("utf-8"))
        store_doctor_notification(msg)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception:
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def start_notification_listener():
    rabbitmq_url = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    while True:
        connection = None
        try:
            params = pika.URLParameters(rabbitmq_url)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.exchange_declare(exchange=SERVICE_EXCHANGE, exchange_type="direct", durable=True)
            channel.queue_declare(queue=DOCTOR_NOTIFICATION_QUEUE, durable=True)
            channel.queue_bind(exchange=SERVICE_EXCHANGE, queue=DOCTOR_NOTIFICATION_QUEUE, routing_key=DOCTOR_ROUTING_KEY)
            channel.basic_qos(prefetch_count=10)
            channel.basic_consume(queue=DOCTOR_NOTIFICATION_QUEUE, on_message_callback=doctor_notification_callback)
            channel.start_consuming()
        except Exception:
            time.sleep(5)
        finally:
            if connection and connection.is_open:
                connection.close()


def has_conflict(appointment_url, doctor_id, slot_start, slot_end):
    conflict_resp = req(
        "GET",
        f"{appointment_url}/appointment/conflict",
        params={
            "doctorID": doctor_id,
            "slotStart": slot_start.isoformat(),
            "slotEnd": slot_end.isoformat(),
        },
    )
    conflict_resp.raise_for_status()
    return bool(conflict_resp.json().get("conflict"))


def get_scheduled_doctors(doctor_url, slot_start, slot_end, specialisation=None, doctor_id=None):
    params = {"slotStart": slot_start.isoformat(), "slotEnd": slot_end.isoformat()}
    if specialisation:
        params["specialisation"] = specialisation
    if doctor_id:
        params["doctorID"] = doctor_id
    resp = req("GET", f"{doctor_url}/doctor-schedule/available", params=params)
    resp.raise_for_status()
    return resp.json().get("doctors", [])


def get_doctor_slots(doctor_url, doctor_id, from_dt, exclude_start=None, exclude_end=None, limit=5):
    params = {"from": from_dt.isoformat(), "limit": limit}
    if exclude_start and exclude_end:
        params["excludeStart"] = exclude_start.isoformat()
        params["excludeEnd"] = exclude_end.isoformat()
    resp = req("GET", f"{doctor_url}/doctor-schedule/doctor/{doctor_id}/alternatives", params=params)
    if not resp.ok:
        return []
    return resp.json().get("slots", [])


def build_alternatives(doctor_url, appointment_url, slot_start, slot_end, specialisation=None, preferred_doctor_id=None):
    same_slot_candidates = get_scheduled_doctors(doctor_url, slot_start, slot_end, specialisation=specialisation)

    same_slot_doctors = []
    for doc in same_slot_candidates:
        doc_id = doc.get("doctorID")
        if not doc_id:
            continue
        if preferred_doctor_id and doc_id == preferred_doctor_id:
            continue
        if has_conflict(appointment_url, doc_id, slot_start, slot_end):
            continue
        same_slot_doctors.append(
            {
                "doctorID": doc_id,
                "name": doc.get("name"),
                "specialisation": doc.get("specialisation"),
            }
        )

    preferred_slots = []
    if preferred_doctor_id:
        raw_slots = get_doctor_slots(
            doctor_url,
            preferred_doctor_id,
            from_dt=slot_start,
            exclude_start=slot_start,
            exclude_end=slot_end,
            limit=8,
        )
        for slot in raw_slots:
            alt_start = parse_datetime(slot.get("slotStart"))
            alt_end = parse_datetime(slot.get("slotEnd"))
            if not alt_start or not alt_end:
                continue
            if has_conflict(appointment_url, preferred_doctor_id, alt_start, alt_end):
                continue
            preferred_slots.append(slot)
            if len(preferred_slots) >= 5:
                break

    any_slots = []
    seen = set()
    for doc in same_slot_candidates[:6]:
        doc_id = doc.get("doctorID")
        if not doc_id:
            continue
        raw_slots = get_doctor_slots(
            doctor_url,
            doc_id,
            from_dt=slot_start,
            exclude_start=slot_start,
            exclude_end=slot_end,
            limit=4,
        )
        for slot in raw_slots:
            alt_start = parse_datetime(slot.get("slotStart"))
            alt_end = parse_datetime(slot.get("slotEnd"))
            if not alt_start or not alt_end:
                continue
            key = f"{doc_id}:{slot.get('slotStart')}:{slot.get('slotEnd')}"
            if key in seen:
                continue
            if has_conflict(appointment_url, doc_id, alt_start, alt_end):
                continue
            seen.add(key)
            any_slots.append(
                {
                    "doctorID": doc_id,
                    "doctorName": doc.get("name"),
                    "specialisation": doc.get("specialisation"),
                    "slotID": slot.get("slotID"),
                    "slotStart": slot.get("slotStart"),
                    "slotEnd": slot.get("slotEnd"),
                }
            )
            if len(any_slots) >= 8:
                break
        if len(any_slots) >= 8:
            break

    return {
        "sameSlotDoctors": same_slot_doctors[:6],
        "preferredDoctorOtherTimes": preferred_slots[:5],
        "otherTimeslots": any_slots[:8],
    }


def notify_patient(notification_url, patient, appointment, status, cancellation_reason=None, alternatives=None):
    receiver = patient.get("email")
    mobile = patient.get("phone")
    patient_id = patient.get("patientID") or appointment.get("patientID")
    doctor_id = appointment.get("doctorID")
    appt_id = appointment.get("appointmentID")
    slot_start = appointment.get("slotStart") or appointment.get("dateTime")

    if status == "confirmed":
        event_type = "appointment_confirmed"
        subject = "MediConnect Appointment Confirmed"
        content = (
            f"Your appointment on {slot_start} with doctor {doctor_id} has been confirmed. "
            "Please proceed to join at your scheduled time."
        )
        sms = "Your MediConnect appointment has been confirmed."
    else:
        event_type = "appointment_cancelled"
        subject = "MediConnect Appointment Update"
        content = "Your appointment was rejected by the doctor."
        if cancellation_reason:
            content += f" Reason: {cancellation_reason}."
        if alternatives and alternatives.get("sameSlotDoctors"):
            content += " Other available doctors are available at the same slot."
        elif alternatives and alternatives.get("otherTimeslots"):
            content += " Alternative timeslots are available."
        sms = "Your appointment was rejected. Please review alternative doctors/timeslots."

    payload = {
        "event_type": event_type,
        "patientID": patient_id,
        "patientName": patient.get("name"),
        "appointmentID": appt_id,
        "slotStart": slot_start,
        "reason": cancellation_reason,
        "receiver": receiver,
        "subject": subject,
        "content": content,
        "mobile": mobile,
        "message": sms,
    }
    try:
        resp = req("POST", f"{notification_url}/notify/send", json=payload)
        return resp.ok
    except Exception:
        return False


@app.errorhandler(Exception)
def handle_exception(err):
    code = 503 if isinstance(err, RuntimeError) else getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "book-appointment"})


@app.route("/book-appointment", methods=["POST"])
def book_appointment():
    body = request.get_json(silent=True) or {}
    patient_id = body.get("patientID")
    preferred_doctor_id = body.get("doctorID")
    specialisation = body.get("specialisation")
    reason = body.get("reason")
    doctor_preference = body.get("doctorPreference", "preferred" if preferred_doctor_id else "any")
    slot_start, slot_end = get_slot_from_payload(body)

    if not patient_id or not slot_start or not slot_end:
        return jsonify({"error": "patientID and slotStart/slotEnd (or dateTime) are required"}), 400

    patient_url, doctor_url, appointment_url, _ = get_service_urls()
    patient_resp = req("GET", f"{patient_url}/patient/{patient_id}")
    if patient_resp.status_code == 404:
        return jsonify({"error": "Patient not found"}), 404
    patient_resp.raise_for_status()

    selected_doctor = None
    preferred_unavailable = False

    if preferred_doctor_id:
        preferred_candidates = get_scheduled_doctors(
            doctor_url,
            slot_start,
            slot_end,
            specialisation=None,
            doctor_id=preferred_doctor_id,
        )
        if preferred_candidates:
            candidate = preferred_candidates[0]
            if not has_conflict(appointment_url, preferred_doctor_id, slot_start, slot_end):
                selected_doctor = candidate
            else:
                preferred_unavailable = True
        else:
            preferred_unavailable = True

        if preferred_unavailable and doctor_preference != "any":
            alternatives = build_alternatives(
                doctor_url=doctor_url,
                appointment_url=appointment_url,
                slot_start=slot_start,
                slot_end=slot_end,
                specialisation=specialisation,
                preferred_doctor_id=preferred_doctor_id,
            )
            return jsonify(
                {
                    "error": "Preferred doctor is not available for the selected slot",
                    "alternatives": alternatives,
                }
            ), 409

    if not selected_doctor:
        candidates = get_scheduled_doctors(
            doctor_url,
            slot_start,
            slot_end,
            specialisation=specialisation,
        )
        for doctor in candidates:
            doc_id = doctor.get("doctorID")
            if not doc_id:
                continue
            if has_conflict(appointment_url, doc_id, slot_start, slot_end):
                continue
            selected_doctor = doctor
            break

    if not selected_doctor:
        alternatives = build_alternatives(
            doctor_url=doctor_url,
            appointment_url=appointment_url,
            slot_start=slot_start,
            slot_end=slot_end,
            specialisation=specialisation,
            preferred_doctor_id=preferred_doctor_id,
        )
        return jsonify({"error": "No available doctor for this timeslot", "alternatives": alternatives}), 409

    # Acquire a per-slot Redis lock to prevent two concurrent bookings for the same doctor+slot.
    doctor_id_for_lock = selected_doctor.get("doctorID")
    lock_key = f"slot_lock:{doctor_id_for_lock}:{slot_start.isoformat()}"
    lock_acquired = False
    try:
        r = get_redis()
        lock_acquired = bool(r.set(lock_key, "1", nx=True, ex=30))
    except Exception:
        lock_acquired = True  # Redis unavailable — allow the booking (degraded mode)

    if not lock_acquired:
        return jsonify({"error": "This slot is being booked by another user. Please try again in a moment."}), 409

    try:
        # Re-check conflict inside the lock to catch the race condition.
        if has_conflict(appointment_url, doctor_id_for_lock, slot_start, slot_end):
            return jsonify({"error": "Doctor is no longer available for this slot", "alternatives": []}), 409

        appointment_resp = req(
            "POST",
            f"{appointment_url}/appointment",
            json={
                "patientID": patient_id,
                "doctorID": doctor_id_for_lock,
                "dateTime": slot_start.isoformat(),
                "slotStart": slot_start.isoformat(),
                "slotEnd": slot_end.isoformat(),
                "specialisation": specialisation,
                "reason": reason,
                "doctorPreference": doctor_preference,
                "status": "pending",
            },
        )
        appointment_resp.raise_for_status()
        appointment = appointment_resp.json()

        try:
            req(
                "PUT",
                f"{doctor_url}/doctor-schedule/slot",
                json={
                    "doctorID": doctor_id_for_lock,
                    "slotStart": slot_start.isoformat(),
                    "status": "booked",
                },
            )
        except Exception:
            pass  # Non-fatal: appointment is created, schedule update is best-effort

        # Send email + Telegram notification for appointment booking
        _, _, _, notification_url = get_service_urls()
        patient_resp = req("GET", f"{patient_url}/patient/{patient_id}")
        patient_data = patient_resp.json() if patient_resp.ok else {}
        try:
            slot_display = slot_start.strftime("%d %b %Y %I:%M %p").lower()
            req("POST", f"{notification_url}/notify/send", json={
                "event_type": "appointment_booked",
                "patientID": patient_id,
                "patientName": patient_data.get("name"),
                "appointmentID": appointment.get("appointmentID"),
                "slotStart": slot_start.isoformat(),
                "receiver": patient_data.get("email"),
                "subject": "MediConnect: Appointment Requested",
                "content": (
                    f"Hi {patient_data.get('name', 'there')}, your appointment has been requested for "
                    f"{slot_display}. Awaiting doctor confirmation."
                ),
                "mobile": patient_data.get("phone"),
                "message": f"[MediConnect] Appointment requested for {slot_display}.",
            })
        except Exception:
            pass

        # Push real-time SSE notifications to the patient and doctor
        sse_url = os.environ.get("SSE_SERVICE_URL", "http://sse-service:5060").rstrip("/")
        slot_display = slot_start.strftime("%d %b %Y %I:%M %p").lower()
        try:
            req("POST", f"{sse_url}/sse/notify", json={
                "userID": patient_id,
                "event": "appointment_booked",
                "data": {
                    "message": f"Your appointment has been requested for {slot_display}.",
                    "appointmentID": appointment.get("appointmentID"),
                    "doctorID": doctor_id_for_lock,
                    "slotStart": slot_start.isoformat(),
                },
            })
        except Exception:
            pass
        try:
            req("POST", f"{sse_url}/sse/notify", json={
                "userID": doctor_id_for_lock,
                "event": "appointment_requested",
                "data": {
                    "message": f"New appointment request for {slot_display}.",
                    "appointmentID": appointment.get("appointmentID"),
                    "patientID": patient_id,
                    "slotStart": slot_start.isoformat(),
                },
            })
        except Exception:
            pass
    finally:
        try:
            if lock_acquired:
                get_redis().delete(lock_key)
        except Exception:
            pass

    doctor_event = {
        "eventType": "appointment_requested",
        "appointmentID": appointment.get("appointmentID"),
        "patientID": appointment.get("patientID"),
        "doctorID": appointment.get("doctorID"),
        "doctorName": selected_doctor.get("name"),
        "specialisation": selected_doctor.get("specialisation"),
        "slotStart": appointment.get("slotStart") or appointment.get("dateTime"),
        "slotEnd": appointment.get("slotEnd"),
        "reason": reason,
    }
    rabbit_ok, rabbit_error = publish_rabbit_message(DOCTOR_ROUTING_KEY, doctor_event)

    return jsonify(
        {
            "message": "Appointment booked and pending doctor confirmation",
            "appointmentID": appointment.get("appointmentID"),
            "status": appointment.get("status"),
            "patientID": appointment.get("patientID"),
            "doctorID": appointment.get("doctorID"),
            "slotStart": appointment.get("slotStart") or appointment.get("dateTime"),
            "slotEnd": appointment.get("slotEnd"),
            "rabbitPublished": rabbit_ok,
            "rabbitError": rabbit_error,
        }
    ), 201


@app.route("/book-appointment/alternatives", methods=["POST"])
def get_alternatives():
    body = request.get_json(silent=True) or {}
    slot_start, slot_end = get_slot_from_payload(body)
    if not slot_start or not slot_end:
        return jsonify({"error": "slotStart/slotEnd or dateTime are required"}), 400

    _, doctor_url, appointment_url, _ = get_service_urls()
    alternatives = build_alternatives(
        doctor_url=doctor_url,
        appointment_url=appointment_url,
        slot_start=slot_start,
        slot_end=slot_end,
        specialisation=body.get("specialisation"),
        preferred_doctor_id=body.get("doctorID"),
    )
    return jsonify({"alternatives": alternatives})


@app.route("/book-appointment/respond", methods=["POST"])
def respond_to_appointment():
    body = request.get_json(silent=True) or {}
    appointment_id = body.get("appointmentID")
    status = body.get("status")
    cancellation_reason = body.get("cancellationReason")

    if not appointment_id or status not in ["confirmed", "cancelled"]:
        return jsonify({"error": "appointmentID and status (confirmed/cancelled) are required"}), 400

    patient_url, doctor_url, appointment_url, notification_url = get_service_urls()

    appointment_resp = req("GET", f"{appointment_url}/appointment/{appointment_id}")
    if appointment_resp.status_code == 404:
        return jsonify({"error": "Appointment not found"}), 404
    appointment_resp.raise_for_status()
    appointment = appointment_resp.json()

    alternatives = None
    if status == "cancelled":
        slot_start = parse_datetime(appointment.get("slotStart") or appointment.get("dateTime"))
        slot_end = parse_datetime(appointment.get("slotEnd"))
        if slot_start and not slot_end:
            slot_end = slot_start + timedelta(minutes=30)
        if slot_start and slot_end:
            alternatives = build_alternatives(
                doctor_url=doctor_url,
                appointment_url=appointment_url,
                slot_start=slot_start,
                slot_end=slot_end,
                specialisation=appointment.get("specialisation"),
                preferred_doctor_id=appointment.get("doctorID"),
            )

    payload = {"status": status}
    if status == "cancelled":
        payload["cancellationReason"] = cancellation_reason or "Doctor rejected appointment"
        payload["alternativeOptions"] = alternatives

    update_resp = req("PUT", f"{appointment_url}/appointment/{appointment_id}", json=payload)
    update_resp.raise_for_status()
    updated_appointment = update_resp.json()

    patient_notification_sent = False
    try:
        patient_resp = req("GET", f"{patient_url}/patient/{updated_appointment.get('patientID')}")
        if patient_resp.ok:
            patient = patient_resp.json()
            patient_notification_sent = notify_patient(
                notification_url=notification_url,
                patient=patient,
                appointment=updated_appointment,
                status=status,
                cancellation_reason=cancellation_reason,
                alternatives=alternatives,
            )
    except Exception:
        patient_notification_sent = False

    publish_rabbit_message(
        "appointment.status.updated",
        {
            "eventType": "appointment_status_updated",
            "appointmentID": updated_appointment.get("appointmentID"),
            "doctorID": updated_appointment.get("doctorID"),
            "patientID": updated_appointment.get("patientID"),
            "status": updated_appointment.get("status"),
        },
    )

    return jsonify(
        {
            "message": f"Appointment {status}",
            "appointment": updated_appointment,
            "alternatives": alternatives,
            "patientNotified": patient_notification_sent,
        }
    )


@app.route("/book-appointment/symptom-check", methods=["POST"])
def symptom_check():
    """
    Proxy endpoint so the frontend never calls openai-wrapper directly.
    Accepts { symptoms, patientID? } and enriches the AI call with
    the patient's allergies and past medical history fetched from patient-service.
    """
    body = request.get_json(silent=True) or {}
    symptoms = body.get("symptoms")
    patient_id = body.get("patientID")

    if not symptoms:
        return jsonify({"error": "symptoms is required"}), 400

    openai_url = os.environ.get("OPENAI_WRAPPER_URL", "http://openai-wrapper:5021").rstrip("/")
    patient_url_base = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")

    allergies, past_history = [], []
    if patient_id:
        try:
            p_resp = req("GET", f"{patient_url_base}/patient/{patient_id}")
            if p_resp.ok:
                p_data = p_resp.json()
                allergies = p_data.get("allergies") or []
                past_history = p_data.get("pastHistory") or []
        except Exception:
            pass

    ai_resp = req(
        "POST",
        f"{openai_url}/openai/symptom-check",
        json={"symptoms": symptoms, "allergies": allergies, "pastHistory": past_history},
    )
    return (ai_resp.content, ai_resp.status_code, {"Content-Type": "application/json"})


@app.route("/book-appointment/doctor-notifications/<doctor_id>", methods=["GET"])
def get_doctor_notifications(doctor_id):
    consume = str(request.args.get("consume", "false")).lower() == "true"
    with _doctor_notifications_lock:
        notifications = list(_doctor_notifications.get(doctor_id, []))
        if consume:
            _doctor_notifications[doctor_id] = []
    return jsonify({"doctorID": doctor_id, "count": len(notifications), "notifications": notifications})


def boot_listener():
    if os.environ.get("DISABLE_BOOKING_RABBIT_CONSUMER", "0") == "1":
        return
    t = threading.Thread(target=start_notification_listener, daemon=True)
    t.start()


boot_listener()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5033)
