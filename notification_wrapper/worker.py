import os
import json
import logging
import requests
import pika
from dotenv import load_dotenv

# Optional: set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# Extract essential environment variables
RABBITMQ_URL = os.getenv("RABBITMQ_URL")
SMU_API_URL = os.getenv("SMU_API_URL")
SMU_SMS_API_URL = os.getenv("SMU_SMS_API_URL")
SMU_X_CONTACTS_KEY = os.getenv("SMU_X_CONTACTS_KEY")

if not all([RABBITMQ_URL, SMU_API_URL, SMU_X_CONTACTS_KEY]):
    raise ValueError("Missing essential environment variables: RABBITMQ_URL, SMU_API_URL, or SMU_X_CONTACTS_KEY")
if not SMU_SMS_API_URL and "SendEmail" in SMU_API_URL:
    SMU_SMS_API_URL = SMU_API_URL.replace("SendEmail", "SendSMS")


def post_to_smu(url, payload):
    headers = {
        "Content-Type": "application/json",
        "X-Contacts-Key": SMU_X_CONTACTS_KEY,
    }
    return requests.post(url, json=payload, headers=headers, timeout=10)


def send_email(message):
    email_payload = message.get("emailPayload")
    if not email_payload:
        receiver = message.get("receiver")
        subject = message.get("subject")
        content = message.get("content")
        if not all([receiver, subject, content]):
            return False, "Email fields missing (receiver, subject, content)"
        email_payload = {
            "emailAddress": receiver,
            "emailSubject": subject,
            "emailBody": content,
        }
    response = post_to_smu(SMU_API_URL, email_payload)
    return response.status_code in [200, 201, 202], f"Email status={response.status_code}, body={response.text}"


def send_sms(message):
    if not SMU_SMS_API_URL:
        return False, "SMU_SMS_API_URL not configured"
    sms_payload = message.get("smsPayload")
    if not sms_payload:
        phone = message.get("phoneNumber")
        sms_text = message.get("smsMessage")
        if not all([phone, sms_text]):
            return False, "SMS fields missing (phoneNumber, smsMessage)"
        # phone_field = os.environ.get("SMS_PHONE_FIELD", "mobile")
        # message_field = os.environ.get("SMS_MESSAGE_FIELD", "message")
        sms_payload = {
            "mobile": phone,
            "message": sms_text,
        }
    response = post_to_smu(SMU_SMS_API_URL, sms_payload)
    return response.status_code in [200, 201, 202], f"SMS status={response.status_code}, body={response.text}"

def callback(ch, method, properties, body):
    """
    Callback triggered when a message is received from the queue.
    Performs a fire-and-forget HTTP POST to the SMU API.
    """
    try:
        # 1. Parse the incoming JSON message
        message = json.loads(body.decode('utf-8'))
        
        # --- NEW: Event-Driven Template Engine ---
        event_type = message.get("event_type")
        if event_type:
            patient_name = message.get("patientName", "Customer")
            order_id = message.get("orderID", "Unknown")
            
            if event_type == "payment_successful":
                message["receiver"] = message.get("patientEmail")
                message["subject"] = "MediConnect: Payment Successful"
                message["content"] = f"Hi {patient_name}, your payment for order {order_id} was successful. We are preparing your medication for delivery."
                message["channel"] = "email"
            elif event_type == "payment_refunded":
                amount = message.get("amount", 0)
                refund_amount_str = f"{amount / 100:.2f}"
                message["receiver"] = message.get("patientEmail")
                message["subject"] = "MediConnect: Order Refunded"
                message["content"] = f"Hi {patient_name}, unfortunately we could not find an available rider for order {order_id}. Your payment of ${refund_amount_str} has been fully refunded."
                message["channel"] = "email"
        # -----------------------------------------

        channel = (message.get("channel") or "email").lower()
        logging.info(f"Processing message with channel='{channel}'")

        ok_email = True
        ok_sms = True
        details = []

        if channel in ("email", "both"):
            ok_email, detail = send_email(message)
            details.append(detail)

        if channel in ("sms", "both"):
            ok_sms, detail = send_sms(message)
            details.append(detail)

        if ok_email and ok_sms:
            logging.info("Notification succeeded: %s", " | ".join(details))
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            logging.error("Notification failed: %s", " | ".join(details))
            logging.info("Requeueing the message for retry...")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    except json.JSONDecodeError:
        logging.error(f"Failed to decode message body as JSON: {body}")
        # Discard unparseable JSON messages (no point retrying bad format)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except requests.RequestException as e:
        logging.error(f"HTTP Request to SMU API failed: {e}")
        # Requeue the message on network errors
        logging.info("Requeueing the message for retry due to network error...")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        # Requeue on unexpected errors
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def start_worker():
    """
    Connects to RabbitMQ and starts consuming messages from the notification queue.
    """
    logging.info("Connecting to RabbitMQ...")
    parameters = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    # Declare exchange and queue to ensure they exist (durable=True as requested for queue)
    exchange_name = "service_exchange"
    queue_name = "notification_queue"
    routing_key = "notification"

    channel.exchange_declare(exchange=exchange_name, exchange_type='direct', durable=True)
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)

    channel.basic_qos(prefetch_count=1)
    # Note: auto_ack=False because we manually ack in the finally block
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    logging.info(f"Worker started. Listening to '{queue_name}' queue... To exit press CTRL+C")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        logging.info("Worker interrupted by user. Stopping...")
        channel.stop_consuming()
    finally:
        connection.close()

if __name__ == "__main__":
    start_worker()
