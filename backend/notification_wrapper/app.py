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
_worker_started = False
_worker_lock = threading.Lock()


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


def send_sms(phone_number=None, sms_message=None, sms_payload=None):
    sms_url = os.environ.get("SMU_SMS_API_URL")
    if not sms_url:
        email_url = os.environ.get("SMU_API_URL", "")
        sms_url = email_url.replace("SendEmail", "SendSMS") if "SendEmail" in email_url else None
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


def fetch_patient_email(patient_id):
    base = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")
    resp = requests.get(f"{base}/patient/{patient_id}", timeout=10)
    resp.raise_for_status()
    return resp.json().get("email")


def _first_non_empty(message, keys, default=None):
    for key in keys:
        val = message.get(key)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _normalize_message(message):
    normalized = dict(message or {})
    event_type = normalized.get("event_type")
    patient_name = _first_non_empty(normalized, ["patientName", "name"], "Customer")
    order_id = _first_non_empty(normalized, ["orderID", "orderId"], "Unknown")

    # Event templates
    if event_type == "payment_successful":
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Payment Successful"
        normalized["content"] = (
            f"Hi {patient_name}, your payment for order {order_id} was successful. "
            "We are preparing your medication for delivery."
        )
        normalized["channel"] = "email"
    elif event_type == "payment_refunded":
        amount = float(_first_non_empty(normalized, ["amount"], 0) or 0)
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Order Refunded"
        normalized["content"] = (
            f"Hi {patient_name}, unfortunately we could not find an available rider for order {order_id}. "
            f"Your payment of ${amount / 100:.2f} has been fully refunded."
        )
        normalized["channel"] = "email"
    elif event_type == "rider_assigned":
        rider_name = _first_non_empty(normalized, ["riderName"], "our rider")
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Rider Assigned"
        normalized["content"] = (
            f"Hi {patient_name}, {rider_name} has been assigned to order {order_id} and is on the way."
        )
        normalized["channel"] = "email"
    elif event_type == "order_delivered":
        normalized["receiver"] = _first_non_empty(normalized, ["patientEmail", "email", "receiver"])
        normalized["subject"] = "MediConnect: Order Delivered"
        normalized["content"] = f"Hi {patient_name}, order {order_id} has been delivered successfully."
        normalized["channel"] = "email"

    # Legacy payload compatibility
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
            sms_ok, _ = send_sms(
                phone_number=message.get("mobile"),
                sms_message=message.get("message"),
                sms_payload=message.get("smsPayload"),
            )

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


@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "notification-wrapper"})


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
    send_sms(phone_number=phone_number, sms_message=sms_message, sms_payload=sms_payload)
    return jsonify({"message": "SMS sent", "phoneNumber": phone_number})


@app.route("/notify/send", methods=["POST"])
def notify_send():
    body = request.get_json(silent=True) or {}
    email_sent = False
    sms_sent = False

    receiver = body.get("receiver")
    subject = body.get("subject")
    content = body.get("content")
    if receiver and subject and content:
        send_email(receiver, subject, content)
        email_sent = True

    phone_number = body.get("mobile")
    sms_message = body.get("message")
    sms_payload = body.get("smsPayload")
    if sms_payload or (phone_number and sms_message):
        send_sms(phone_number=phone_number, sms_message=sms_message, sms_payload=sms_payload)
        sms_sent = True

    if not email_sent and not sms_sent:
        return jsonify({"error": "No valid email or SMS payload provided."}), 400
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
    return jsonify({"message": "Order-ready notification sent", "receiver": receiver})


if __name__ == "__main__":
    start_worker_thread()
    app.run(host="0.0.0.0", port=5011)
