import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


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


def fetch_patient_email(patient_id):
    base = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")
    resp = requests.get(f"{base}/patient/{patient_id}", timeout=10)
    resp.raise_for_status()
    return resp.json().get("email")


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
    app.run(host="0.0.0.0", port=5011)
