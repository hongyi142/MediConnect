import os
import json
import logging
import threading
import time

import pika
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://rabbitmq:5672/")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PATIENT_SERVICE_URL = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030")

_worker_started = False
_worker_lock = threading.Lock()


def send_telegram_message(chat_id, text):
    """Send a message to a Telegram chat ID via the Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, json=payload, timeout=10)
    if not resp.ok:
        try:
            error_data = resp.json()
            error_msg = error_data.get("description", resp.text)
        except Exception:
            error_msg = resp.text
        raise RuntimeError(f"Telegram API Error: {error_msg}")
    return resp.json()


def fetch_patient_telegram_id(patient_id):
    """Fetch the patient's telegramChatID from the patient-service."""
    base = PATIENT_SERVICE_URL.rstrip("/")
    resp = requests.get(f"{base}/patient/{patient_id}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return (
        data.get("telegramChatID") or 
        data.get("chatid") or 
        data.get("chatId") or 
        data.get("telegram_chat_id")
    )


def _first_non_empty(message, keys, default=None):
    for key in keys:
        val = message.get(key)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _build_telegram_text(message):
    """Compose a Telegram-friendly HTML message from the notification payload."""
    event_type = message.get("event_type")
    patient_name = _first_non_empty(message, ["patientName", "name"], "Customer")
    order_id = _first_non_empty(message, ["orderID", "orderId"], "Unknown")

    if event_type == "payment_successful":
        return (
            f"✅ <b>Payment Successful</b>\n"
            f"Hi {patient_name}, your payment for order <b>{order_id}</b> was successful. "
            "We are preparing your medication for delivery."
        )
    elif event_type == "payment_refunded":
        amount = float(_first_non_empty(message, ["amount"], 0) or 0)
        return (
            f"↩️ <b>Order Refunded</b>\n"
            f"Hi {patient_name}, we could not find an available rider for order <b>{order_id}</b>. "
            f"Your payment of <b>${amount / 100:.2f}</b> has been fully refunded."
        )
    elif event_type == "rider_assigned":
        rider_name = _first_non_empty(message, ["riderName"], "our rider")
        return (
            f"🏍️ <b>Rider Assigned</b>\n"
            f"Hi {patient_name}, <b>{rider_name}</b> has been assigned to order <b>{order_id}</b> and is on the way."
        )
    elif event_type == "order_delivered":
        return (
            f"📦 <b>Order Delivered</b>\n"
            f"Hi {patient_name}, order <b>{order_id}</b> has been delivered successfully."
        )
    elif event_type == "rider_near_destination":
        return (
            f"🔔 <b>Rider Almost There</b>\n"
            f"Hi {patient_name}, your rider is less than 500m away! "
            f"Please get ready to receive order <b>{order_id}</b>."
        )
    elif event_type == "appointment_booked":
        appt_id = _first_non_empty(message, ["appointmentID", "apptID"], "Unknown")
        slot = _first_non_empty(message, ["slotStart", "dateTime"], "")
        slot_display = slot[:16].replace("T", " ") + " UTC" if slot else "your scheduled time"
        return (
            f"📅 <b>Appointment Requested</b>\n"
            f"Hi {patient_name}, your appointment (<b>{appt_id}</b>) has been requested for "
            f"<b>{slot_display}</b>. Awaiting doctor confirmation."
        )
    elif event_type == "appointment_confirmed":
        appt_id = _first_non_empty(message, ["appointmentID", "apptID"], "Unknown")
        slot = _first_non_empty(message, ["slotStart", "dateTime"], "")
        slot_display = slot[:16].replace("T", " ") + " UTC" if slot else "your scheduled time"
        return (
            f"✅ <b>Appointment Confirmed</b>\n"
            f"Hi {patient_name}, your appointment (<b>{appt_id}</b>) on <b>{slot_display}</b> "
            "has been confirmed. Please log in at your scheduled time to join."
        )
    elif event_type == "appointment_cancelled":
        appt_id = _first_non_empty(message, ["appointmentID", "apptID"], "Unknown")
        reason = _first_non_empty(message, ["reason", "cancellationReason"], "")
        text = (
            f"❌ <b>Appointment Cancelled</b>\n"
            f"Hi {patient_name}, appointment <b>{appt_id}</b> has been cancelled."
        )
        if reason:
            text += f" Reason: {reason}."
        return text
    elif event_type == "order_pending_payment":
        total = float(_first_non_empty(message, ["totalAmount", "amount"], 0) or 0)
        return (
            f"💊 <b>Order Pending Payment</b>\n"
            f"Hi {patient_name}, your consultation is complete. Order <b>{order_id}</b> "
            f"totalling <b>${total:.2f}</b> is pending payment."
        )
    elif event_type == "appointment_reminder":
        label = _first_non_empty(message, ["label"], "soon")
        slot = _first_non_empty(message, ["slotStart", "dateTime"], "")
        slot_display = slot[:16].replace("T", " ") + " UTC" if slot else "your scheduled time"
        return (
            f"⏰ <b>Appointment Reminder</b>\n"
            f"Hi {patient_name}, your appointment is in <b>{label}</b> (at {slot_display}). "
            "Log in to MediConnect to join."
        )
    else:
        # Fallback: use subject/content or body from legacy payloads
        subject = message.get("subject", "MediConnect Notification")
        content = message.get("content") or message.get("body") or message.get("message", "")
        return f"<b>{subject}</b>\n{content}"


def _process_queue_message(ch, method, body):
    try:
        raw = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        logging.error("Telegram worker received invalid JSON: %s", body)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    patient_id = _first_non_empty(raw, ["patientID", "patientId"])
    telegram_chat_id = _first_non_empty(raw, ["telegramChatID", "telegram_chat_id"])

    # Attempt to resolve chat ID from patient-service if not in payload
    if not telegram_chat_id and patient_id:
        try:
            telegram_chat_id = fetch_patient_telegram_id(patient_id)
        except Exception as exc:
            logging.warning("Could not fetch telegramChatID for patient %s: %s", patient_id, exc)

    if not telegram_chat_id:
        logging.info("No telegramChatID found for this notification — skipping. event_type=%s", raw.get("event_type"))
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    text = _build_telegram_text(raw)

    try:
        send_telegram_message(telegram_chat_id, text)
        logging.info("Telegram message sent. chat_id=%s event_type=%s", telegram_chat_id, raw.get("event_type"))
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except requests.RequestException as exc:
        logging.error("Telegram send failed: %s", exc)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as exc:
        logging.error("Unexpected Telegram worker error: %s", exc)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def run_telegram_worker():
    """Consume from a dedicated telegram_queue on the same exchange as notification_wrapper."""
    exchange_name = "service_exchange"
    queue_name = "telegram_queue"
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
            logging.info("Telegram worker connected and consuming queue '%s'", queue_name)
            channel.start_consuming()
        except Exception as exc:
            logging.error("Telegram worker disconnected: %s", exc)
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
        thread = threading.Thread(target=run_telegram_worker, daemon=True, name="telegram-worker")
        thread.start()
        _worker_started = True
        logging.info("Telegram worker thread started")


@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "telegram-wrapper"})


@app.route("/notify/telegram", methods=["POST"])
def notify_telegram():
    """Direct HTTP endpoint to send a Telegram message (bypasses queue)."""
    body = request.get_json(silent=True) or {}
    chat_id = body.get("telegramChatID") or body.get("chat_id")
    text = body.get("text") or body.get("message")
    if not chat_id or not text:
        return jsonify({"error": "Provide telegramChatID (or chat_id) and text (or message)."}), 400
    send_telegram_message(chat_id, text)
    return jsonify({"message": "Telegram message sent", "chat_id": chat_id})


if __name__ == "__main__":
    start_worker_thread()
    app.run(host="0.0.0.0", port=5012)
