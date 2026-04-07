import json
import logging
import os
import queue as _queue_mod
import threading
import time
from datetime import datetime, timedelta, timezone

import pika
import redis as _redis_lib
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://rabbitmq:5672/")
_worker_started = False
_worker_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
#  Email / SMS helpers (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def post_to_smu(api_url, payload):
    contact_key = os.environ.get("SMU_X_CONTACTS_KEY")
    if not api_url or not contact_key:
        raise RuntimeError("SMU API URL and SMU_X_CONTACTS_KEY are required")
    headers = {
        "Content-Type": "application/json",
        "X-Contacts-Key": contact_key,
    }
    resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()


def send_email(receiver, subject, content):
    api_url = os.environ.get("SMU_API_URL")
    payload = {
        "emailAddress": receiver,
        "emailSubject": subject,
        "emailBody": content,
    }
    post_to_smu(api_url, payload)
    return True, "email sent"


def _resolve_sms_api_url():
    sms_url = os.environ.get("SMU_SMS_API_URL")
    if sms_url:
        return sms_url
    email_url = os.environ.get("SMU_API_URL", "")
    if "SendEmail" in email_url:
        return email_url.replace("SendEmail", "SendSMS")
    return None


def send_sms(phone_number=None, sms_message=None, sms_payload=None):
    sms_url = _resolve_sms_api_url()
    if sms_payload and isinstance(sms_payload, dict):
        payload = sms_payload
    else:
        phone_field = os.environ.get("SMS_PHONE_FIELD", "mobile")
        message_field = os.environ.get("SMS_MESSAGE_FIELD", "message")
        payload = {
            phone_field: phone_number,
            message_field: sms_message,
        }
    post_to_smu(sms_url, payload)
    return True, "sms sent"


def send_sms_with_fallback(phone_number=None, sms_message=None, sms_payload=None):
    """Attempt SMS with resilient payload-key fallbacks for differing gateway schemas."""
    if sms_payload and isinstance(sms_payload, dict):
        try:
            send_sms(sms_payload=sms_payload)
            return True, None
        except Exception as exc:
            return False, str(exc)

    if not phone_number or not sms_message:
        return False, "mobile/phone and message are required for SMS"

    try:
        send_sms(phone_number=phone_number, sms_message=sms_message)
        return True, None
    except Exception as first_exc:
        sms_url = _resolve_sms_api_url()
        if not sms_url:
            return False, str(first_exc)

        fallback_payloads = [
            {"phone": phone_number, "message": sms_message},
            {"phoneNumber": phone_number, "message": sms_message},
            {"recipient": phone_number, "message": sms_message},
            {"to": phone_number, "message": sms_message},
            {"mobileNumber": phone_number, "message": sms_message},
            {"phone": phone_number, "text": sms_message},
            {"phoneNumber": phone_number, "smsMessage": sms_message},
        ]
        for payload in fallback_payloads:
            try:
                post_to_smu(sms_url, payload)
                logging.info("SMS sent using fallback payload keys: %s", ",".join(payload.keys()))
                return True, None
            except Exception:
                continue
        return False, str(first_exc)


def fetch_patient_email(patient_id):
    base = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")
    resp = requests.get(f"{base}/patient/{patient_id}", timeout=10)
    resp.raise_for_status()
    return resp.json().get("email")


def fetch_patient_info(patient_id):
    """Return full patient dict (email, phone, name, …) or empty dict on failure."""
    try:
        base = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")
        resp = requests.get(f"{base}/patient/{patient_id}", timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return {}


def _publish_to_telegram_queue(payload: dict) -> None:
    """Best-effort: forward a notification payload to the Telegram worker queue."""
    try:
        params = pika.URLParameters(RABBITMQ_URL)
        conn = pika.BlockingConnection(params)
        ch = conn.channel()
        ch.exchange_declare(exchange="service_exchange", exchange_type="direct", durable=True)
        ch.queue_declare(queue="telegram_queue", durable=True)
        ch.queue_bind(exchange="service_exchange", queue="telegram_queue", routing_key="notification")
        ch.basic_publish(
            exchange="service_exchange",
            routing_key="notification",
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        conn.close()
    except Exception as exc:
        logging.warning("Could not forward notification to Telegram queue: %s", exc)


def _first_non_empty(message, keys, default=None):
    for key in keys:
        val = message.get(key)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _parse_iso_datetime(value):
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _format_slot_display(value, fallback="your scheduled time"):
    dt = _parse_iso_datetime(value)
    if not dt:
        return fallback
    return dt.strftime("%d %b %Y %I:%M %p")


def _normalize_message(message):
    normalized = dict(message or {})
    event_type = normalized.get("event_type")
    patient_name = _first_non_empty(normalized, ["patientName", "name"], "Customer")
    order_id = _first_non_empty(normalized, ["orderID", "orderId"], "Unknown")

    if event_type == "payment_successful":
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Payment Successful"
        normalized["content"] = (
            f"Hi {patient_name}, your payment for order {order_id} was successful. "
            "We are preparing your medication for delivery."
        )
        normalized["message"] = f"[MediConnect] Payment for order {order_id} confirmed. Your medication is being prepared."
        normalized["channel"] = "both"
    elif event_type == "payment_refunded":
        amount = float(_first_non_empty(normalized, ["amount"], 0) or 0)
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Order Refunded"
        normalized["content"] = (
            f"Hi {patient_name}, unfortunately we could not find an available rider for order {order_id}. "
            f"Your payment of ${amount / 100:.2f} has been fully refunded."
        )
        normalized["message"] = f"[MediConnect] Order {order_id} refunded. ${amount / 100:.2f} has been returned to you."
        normalized["channel"] = "both"
    elif event_type == "rider_assigned":
        rider_name = _first_non_empty(normalized, ["riderName"], "our rider")
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Rider Assigned"
        normalized["content"] = (
            f"Hi {patient_name}, {rider_name} has been assigned to order {order_id} and is on the way."
        )
        normalized["message"] = f"[MediConnect] {rider_name} is delivering order {order_id} to you now."
        normalized["channel"] = "both"
    elif event_type == "order_delivered":
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Order Delivered"
        normalized["content"] = f"Hi {patient_name}, order {order_id} has been delivered successfully."
        normalized["message"] = f"[MediConnect] Order {order_id} has been delivered. Thank you for using MediConnect!"
        normalized["channel"] = "both"
    elif event_type == "rider_near_destination":
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Rider Almost There"
        normalized["content"] = f"Hi {patient_name}, your rider is less than 500m away for order {order_id}! Please get ready."
        normalized["message"] = f"[MediConnect] Your rider is almost at your door for order {order_id}."
        normalized["channel"] = "both"
    elif event_type == "appointment_booked":
        appt_id = _first_non_empty(normalized, ["appointmentID", "apptID"], "Unknown")
        slot = _first_non_empty(normalized, ["slotStart", "dateTime"], "")
        slot_display = _format_slot_display(slot)
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Appointment Requested"
        normalized["content"] = f"Hi {patient_name}, your appointment ({appt_id}) has been requested for {slot_display}. Awaiting doctor confirmation."
        normalized["message"] = f"[MediConnect] Appointment {appt_id} requested for {slot_display}. Awaiting confirmation."
        normalized["channel"] = "both"
    elif event_type == "appointment_confirmed":
        appt_id = _first_non_empty(normalized, ["appointmentID", "apptID"], "Unknown")
        slot = _first_non_empty(normalized, ["slotStart", "dateTime"], "")
        slot_display = _format_slot_display(slot)
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Appointment Confirmed"
        normalized["content"] = f"Hi {patient_name}, your appointment ({appt_id}) on {slot_display} has been confirmed."
        normalized["message"] = f"[MediConnect] Appointment {appt_id} confirmed for {slot_display}."
        normalized["channel"] = "both"
    elif event_type == "appointment_cancelled":
        appt_id = _first_non_empty(normalized, ["appointmentID", "apptID"], "Unknown")
        reason = _first_non_empty(normalized, ["reason", "cancellationReason"], "")
        content = f"Hi {patient_name}, appointment {appt_id} has been cancelled."
        if reason:
            content += f" Reason: {reason}."
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Appointment Cancelled"
        normalized["content"] = content
        normalized["message"] = f"[MediConnect] Appointment {appt_id} was cancelled."
        normalized["channel"] = "both"
    elif event_type == "order_pending_payment":
        total = float(_first_non_empty(normalized, ["totalAmount", "amount"], 0) or 0)
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = f"MediConnect: Order {order_id} Pending Payment"
        normalized["content"] = f"Hi {patient_name}, your consultation is complete. Order {order_id} totalling ${total:.2f} is pending payment."
        normalized["message"] = f"[MediConnect] Order {order_id} is pending payment (${total:.2f})."
        normalized["channel"] = "both"

    normalized["receiver"] = _first_non_empty(normalized, ["receiver", "email", "patientEmail"])
    normalized["mobile"] = _first_non_empty(normalized, ["mobile", "phone", "patientPhone"])
    if not normalized.get("content") and normalized.get("message"):
        normalized["content"] = normalized.get("message")
    if not normalized.get("subject") and normalized.get("content"):
        normalized["subject"] = "MediConnect: Delivery Update"
    normalized["channel"] = (normalized.get("channel") or "email").lower()
    return normalized


def _process_queue_message(ch, method, body):
    try:
        raw = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        logging.error("Notification worker received invalid JSON: %s", body)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    message = _normalize_message(raw)
    channel = message.get("channel", "email")

    # If SMS is required but phone is missing, try fetching from patient service
    if channel in ("sms", "both") and not message.get("mobile"):
        patient_id = raw.get("patientID") or raw.get("PatientId")
        if patient_id:
            info = fetch_patient_info(patient_id)
            mobile = info.get("phone") or info.get("mobile") or info.get("phoneNumber")
            if mobile:
                message["mobile"] = mobile

    try:
        email_ok = True
        sms_ok = True
        has_delivery = False

        if channel in ("email", "both"):
            if message.get("receiver") and message.get("subject") and message.get("content"):
                has_delivery = True
                email_ok, _ = send_email(
                    message.get("receiver"),
                    message.get("subject"),
                    message.get("content"),
                )
            else:
                email_ok = False

        if channel in ("sms", "both"):
            has_delivery = True
            sms_ok, sms_err = send_sms_with_fallback(
                phone_number=message.get("mobile"),
                sms_message=message.get("message"),
                sms_payload=message.get("smsPayload"),
            )
            if not sms_ok and sms_err:
                logging.warning("SMS delivery failed in worker: %s", sms_err)

        if not has_delivery:
            logging.warning("Notification skipped (no valid channel payload): %s", raw)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if email_ok and sms_ok:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logging.info("Notification sent. event_type=%s", message.get("event_type"))
        else:
            logging.error("Notification failed validation. payload=%s", raw)
            ch.basic_ack(delivery_tag=method.delivery_tag)
    except requests.RequestException as exc:
        logging.error("Notification send request failed: %s", exc)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as exc:
        logging.error("Unexpected notification worker error: %s", exc)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def run_notification_worker():
    exchange_name = "service_exchange"
    queue_name = "notification_queue"
    routing_key = "notification"

    while True:
        connection = None
        channel = None
        try:
            params = pika.URLParameters(RABBITMQ_URL)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.exchange_declare(exchange=exchange_name, exchange_type="direct", durable=True)
            channel.queue_declare(queue=queue_name, durable=True)
            channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=queue_name,
                on_message_callback=lambda ch, method, properties, body: _process_queue_message(ch, method, body),
            )
            logging.info("Notification worker connected and consuming queue '%s'", queue_name)
            channel.start_consuming()
        except Exception as exc:
            logging.error("Notification worker disconnected: %s", exc)
            time.sleep(5)
        finally:
            if channel and channel.is_open:
                try:
                    channel.close()
                except Exception:
                    pass
            if connection and connection.is_open:
                try:
                    connection.close()
                except Exception:
                    pass


def start_worker_thread():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=run_notification_worker, daemon=True, name="notification-worker")
        thread.start()
        _worker_started = True
        logging.info("Notification worker thread started")


# ══════════════════════════════════════════════════════════════════════════════
#  SSE Push Notifications
# ══════════════════════════════════════════════════════════════════════════════

# In-memory registry: userID → list of queue.Queue (one per open browser tab)
_sse_clients: dict = {}
_sse_lock = threading.Lock()
SSE_HEARTBEAT = 25  # seconds; keeps proxies / browsers from dropping the connection


def _sse_register(user_id: str) -> _queue_mod.Queue:
    q: _queue_mod.Queue = _queue_mod.Queue(maxsize=100)
    with _sse_lock:
        _sse_clients.setdefault(user_id, []).append(q)
    return q


def _sse_unregister(user_id: str, q: _queue_mod.Queue) -> None:
    with _sse_lock:
        bucket = _sse_clients.get(user_id)
        if bucket:
            try:
                bucket.remove(q)
            except ValueError:
                pass
            if not bucket:
                del _sse_clients[user_id]


def _sse_push(user_id: str, event: str, data: dict) -> int:
    """Push event to all open connections for user_id. Returns number pushed."""
    with _sse_lock:
        queues = list(_sse_clients.get(user_id, []))
    pushed = 0
    for q in queues:
        try:
            q.put_nowait({"event": event, "data": data})
            pushed += 1
        except _queue_mod.Full:
            pass
    return pushed


def run_sse_consumer():
    """Background thread: consume from sse_exchange and route to SSE clients."""
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
            ch = conn.channel()
            ch.exchange_declare(exchange="sse_exchange", exchange_type="topic", durable=True)
            result = ch.queue_declare(queue="", exclusive=True)
            q_name = result.method.queue
            ch.queue_bind(exchange="sse_exchange", queue=q_name, routing_key="notify.#")

            def on_msg(_ch, _method, _props, body):
                try:
                    msg = json.loads(body)
                    uid = msg.get("userID", "")
                    evt = msg.get("event", "notification")
                    dat = msg.get("data", {})
                    if uid:
                        _sse_push(uid, evt, dat)
                except Exception:
                    pass

            ch.basic_consume(queue=q_name, on_message_callback=on_msg, auto_ack=True)
            ch.start_consuming()
        except Exception:
            time.sleep(5)


# ══════════════════════════════════════════════════════════════════════════════
#  Appointment Reminder Scheduler
# ══════════════════════════════════════════════════════════════════════════════

_reminder_redis = None
_REMINDER_WINDOW = 5  # ± minutes around the 24h / 1h target


def _get_redis():
    global _reminder_redis
    if _reminder_redis is None:
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _reminder_redis = _redis_lib.from_url(redis_url, decode_responses=True)
    return _reminder_redis


def _already_sent(appt_id: str, key: str) -> bool:
    try:
        return bool(_get_redis().get(f"reminder:{appt_id}:{key}"))
    except Exception:
        return False


def _mark_sent(appt_id: str, key: str) -> None:
    try:
        _get_redis().setex(f"reminder:{appt_id}:{key}", 172800, "1")  # 48 h TTL
    except Exception:
        pass


def _fire_reminder(appt: dict, label: str, rkey: str) -> None:
    appt_id = appt.get("appointmentID", "")
    patient_id = appt.get("patientID", "")
    slot_start = appt.get("slotStart") or appt.get("dateTime", "")

    if not appt_id or not patient_id:
        return
    if _already_sent(appt_id, rkey):
        return

    slot_display = _format_slot_display(slot_start)
    message = f"Reminder: your appointment is in {label} (at {slot_display})."

    # Real-time SSE push (instant if patient is online)
    _sse_push(patient_id, "reminder", {
        "message": message,
        "appointmentID": appt_id,
        "slotStart": slot_start,
    })

    # Email fallback (always attempted)
    try:
        email = fetch_patient_email(patient_id)
        if email:
            send_email(
                email,
                f"MediConnect – Appointment reminder ({label})",
                f"{message}\n\nAppointment ID: {appt_id}\n\n"
                "Please log in to MediConnect to view the full details.",
            )
    except Exception as exc:
        logging.warning("Reminder email failed for %s: %s", appt_id, exc)

    # Telegram reminder
    _publish_to_telegram_queue({
        "event_type": "appointment_reminder",
        "patientID": patient_id,
        "appointmentID": appt_id,
        "slotStart": slot_start,
        "label": label,
    })

    _mark_sent(appt_id, rkey)
    logging.info("Sent %s reminder for appointment %s (patient %s)", rkey, appt_id, patient_id)


def check_reminders() -> None:
    appt_url = os.environ.get(
        "APPOINTMENT_SERVICE_URL", "http://appointment-service:5032"
    ).rstrip("/")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reminders = [
        {"key": "24h", "label": "24 hours",   "minutes": 24 * 60},
        {"key": "1h",  "label": "1 hour",     "minutes": 60},
        {"key": "10m", "label": "10 minutes", "minutes": 10},
    ]
    # Query window must cover the earliest checkpoint (10 min) minus the tolerance
    query_from = now + timedelta(minutes=10 - _REMINDER_WINDOW)
    query_to = now + timedelta(hours=24, minutes=_REMINDER_WINDOW)

    try:
        resp = requests.get(
            f"{appt_url}/appointment/upcoming",
            params={
                "status": "confirmed",
                "from": query_from.isoformat(),
                "to": query_to.isoformat(),
            },
            timeout=10,
        )
        if not resp.ok:
            return
        appointments = resp.json().get("appointments", [])
    except Exception as exc:
        logging.warning("Reminder check — could not fetch appointments: %s", exc)
        return

    for appt in appointments:
        raw = appt.get("slotStart") or appt.get("dateTime")
        if not raw:
            continue
        try:
            slot_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if slot_dt.tzinfo:
                slot_dt = slot_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue

        for r in reminders:
            target = slot_dt - timedelta(minutes=r["minutes"])
            if abs((now - target).total_seconds()) <= _REMINDER_WINDOW * 60:
                _fire_reminder(appt, r["label"], r["key"])


_TTL_MINUTES = 10  # Auto-cancel pending appointments after this many minutes


def check_pending_ttl() -> None:
    """Cancel pending appointments that have not been accepted within TTL_MINUTES."""
    appt_url = os.environ.get(
        "APPOINTMENT_SERVICE_URL", "http://appointment-service:5032"
    ).rstrip("/")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=_TTL_MINUTES)

    try:
        resp = requests.get(
            f"{appt_url}/appointment/upcoming",
            params={"status": "pending"},
            timeout=10,
        )
        if not resp.ok:
            return
        appointments = resp.json().get("appointments", [])
    except Exception as exc:
        logging.warning("TTL check — could not fetch pending appointments: %s", exc)
        return

    for appt in appointments:
        appt_id = appt.get("appointmentID", "")
        patient_id = appt.get("patientID", "")
        created_raw = appt.get("createdAt")
        if not appt_id or not created_raw:
            continue

        # Skip if already processed
        try:
            if _get_redis().get(f"ttl_cancelled:{appt_id}"):
                continue
        except Exception:
            pass

        try:
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if created_dt.tzinfo:
                created_dt = created_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue

        if created_dt > cutoff:
            continue  # Not yet expired

        # Cancel the appointment
        try:
            cancel_resp = requests.put(
                f"{appt_url}/appointment/{appt_id}",
                json={
                    "status": "cancelled",
                    "cancellationReason": "Doctor did not respond within 10 minutes. Please book again.",
                },
                timeout=10,
            )
            if not cancel_resp.ok:
                logging.warning("TTL cancel failed for %s: %s", appt_id, cancel_resp.text)
                continue
        except Exception as exc:
            logging.warning("TTL cancel request error for %s: %s", appt_id, exc)
            continue

        # Mark as processed
        try:
            _get_redis().setex(f"ttl_cancelled:{appt_id}", 86400, "1")
        except Exception:
            pass

        # Notify patient via SSE
        slot_start = appt.get("slotStart") or appt.get("dateTime", "")
        slot_display = _format_slot_display(slot_start, fallback="your slot")
        message = (
            f"Your appointment for {slot_display} was automatically cancelled because "
            "the doctor did not respond within 10 minutes. Please book a new appointment."
        )
        _sse_push(patient_id, "appointment_cancelled", {
            "message": message,
            "appointmentID": appt_id,
        })

        # Notify patient via email + SMS
        try:
            info = fetch_patient_info(patient_id)
            email = info.get("email")
            phone = info.get("phone") or info.get("mobile") or info.get("phoneNumber")
            patient_name = info.get("name", "Patient")
            if email:
                send_email(
                    email,
                    "MediConnect – Appointment Auto-Cancelled",
                    f"Hi {patient_name},\n\n{message}\n\nAppointment ID: {appt_id}",
                )
            if phone:
                sms_ok, sms_err = send_sms_with_fallback(phone_number=phone, sms_message=f"[MediConnect] {message}")
                if not sms_ok and sms_err:
                    logging.warning("TTL SMS delivery failed for %s: %s", appt_id, sms_err)

            _publish_to_telegram_queue({
                "event_type": "appointment_cancelled",
                "patientID": patient_id,
                "patientName": patient_name,
                "appointmentID": appt_id,
                "slotStart": slot_start,
                "reason": "Doctor did not respond within 10 minutes",
            })
        except Exception as exc:
            logging.warning("TTL notification failed for %s: %s", appt_id, exc)

        logging.info("Auto-cancelled expired pending appointment %s (patient %s)", appt_id, patient_id)


def start_reminder_scheduler():
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(check_reminders, "interval", minutes=1, id="check_reminders")
    scheduler.add_job(check_pending_ttl, "interval", minutes=1, id="check_pending_ttl")
    scheduler.start()
    logging.info("Reminder + TTL scheduler started (every 1 minute)")


# ══════════════════════════════════════════════════════════════════════════════
#  Flask routes
# ══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    with _sse_lock:
        sse_users = len(_sse_clients)
        sse_conns = sum(len(v) for v in _sse_clients.values())
    return jsonify({
        "status": "ok",
        "service": "notification-wrapper",
        "sse_connected_users": sse_users,
        "sse_total_connections": sse_conns,
    })


# ── SSE endpoints ─────────────────────────────────────────────────────────

@app.route("/sse/stream")
def sse_stream():
    """Browser connects here to receive real-time push events."""
    user_id = request.args.get("userID", "").strip()
    if not user_id:
        return jsonify({"error": "userID is required"}), 400

    q = _sse_register(user_id)

    def generate():
        try:
            yield f"event: connected\ndata: {json.dumps({'userID': user_id})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=SSE_HEARTBEAT)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
                except _queue_mod.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            _sse_unregister(user_id, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx / Kong buffering
            "Connection": "keep-alive",
        },
    )


@app.route("/sse/notify", methods=["POST"])
def sse_notify():
    """Internal: any service POSTs here to push an event to a specific user."""
    body = request.get_json(silent=True) or {}
    user_id = body.get("userID", "").strip()
    event = body.get("event", "notification")
    data = body.get("data", {})
    if not user_id:
        return jsonify({"error": "userID is required"}), 400
    pushed = _sse_push(user_id, event, data)
    return jsonify({"pushed": True, "connections": pushed})


# ── Notification endpoints (unchanged) ───────────────────────────────────

@app.route("/notify/email", methods=["POST"])
def notify_email():
    body = request.get_json(silent=True) or {}
    receiver = body.get("receiver")
    subject = body.get("subject")
    content = body.get("content")
    if not receiver or not subject or not content:
        return jsonify({"error": "Provide receiver, subject and content."}), 400
    send_email(receiver, subject, content)
    return jsonify({"message": "Email sent", "receiver": receiver})


@app.route("/notify/sms", methods=["POST"])
def notify_sms():
    body = request.get_json(silent=True) or {}
    sms_payload = body.get("smsPayload")
    phone_number = body.get("mobile")
    sms_message = body.get("message")
    if not sms_payload and (not phone_number or not sms_message):
        return jsonify({"error": "Provide smsPayload or mobile + message."}), 400
    sms_ok, sms_err = send_sms_with_fallback(
        phone_number=phone_number,
        sms_message=sms_message,
        sms_payload=sms_payload,
    )
    if not sms_ok:
        return jsonify({"error": f"SMS send failed: {sms_err}"}), 502
    return jsonify({"message": "SMS sent", "phoneNumber": phone_number})


@app.route("/notify/send", methods=["POST"])
def notify_send():
    body = request.get_json(silent=True) or {}
    message = _normalize_message(body)
    email_sent = False
    sms_sent = False

    receiver = message.get("receiver")
    subject = message.get("subject")
    content = message.get("content")
    if receiver and subject and content:
        send_email(receiver, subject, content)
        email_sent = True

    phone_number = message.get("mobile")
    sms_message = message.get("message")
    sms_payload = message.get("smsPayload")
    if not phone_number:
        patient_id = message.get("patientID") or message.get("patientId") or message.get("PatientId")
        if patient_id:
            info = fetch_patient_info(patient_id)
            phone_number = info.get("phone") or info.get("mobile") or info.get("phoneNumber")
            if phone_number:
                message["mobile"] = phone_number
    if sms_payload or (phone_number and sms_message):
        sms_sent, sms_err = send_sms_with_fallback(
            phone_number=phone_number,
            sms_message=sms_message,
            sms_payload=sms_payload,
        )
        if not sms_sent and sms_err:
            logging.warning("SMS delivery failed in /notify/send: %s", sms_err)

    if not email_sent and not sms_sent:
        return jsonify({"error": "No valid email or SMS payload provided."}), 400

    # Forward to Telegram if patient is identifiable
    if message.get("patientID") or message.get("event_type"):
        _publish_to_telegram_queue(message)

    return jsonify({"emailSent": email_sent, "smsSent": sms_sent})


@app.route("/notify/order-ready", methods=["POST"])
def notify_order_ready():
    body = request.get_json(silent=True) or {}
    patient_id = body.get("patientID")
    order_id = body.get("orderID")
    total = body.get("totalAmount")
    if not patient_id or not order_id:
        return jsonify({"error": "patientID and orderID are required"}), 400

    receiver = fetch_patient_email(patient_id)
    if not receiver:
        return jsonify({"error": "Patient email not found"}), 404

    subject = f"Order Ready - {order_id}"
    content = f"Your order {order_id} is ready. Total amount: ${float(total or 0):.2f}."
    send_email(receiver, subject, content)

    # Forward to Telegram
    _publish_to_telegram_queue({
        "event_type": "order_pending_payment",
        "patientID": patient_id,
        "orderID": order_id,
        "totalAmount": total,
    })

    return jsonify({"message": "Order-ready notification sent", "receiver": receiver})


if __name__ == "__main__":
    start_worker_thread()
    threading.Thread(target=run_sse_consumer, daemon=True, name="sse-consumer").start()
    start_reminder_scheduler()
    app.run(host="0.0.0.0", port=5011, threaded=True)
