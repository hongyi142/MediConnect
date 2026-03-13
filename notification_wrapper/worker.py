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
SMU_X_CONTACTS_KEY = os.getenv("SMU_X_CONTACTS_KEY")

if not all([RABBITMQ_URL, SMU_API_URL, SMU_X_CONTACTS_KEY]):
    raise ValueError("Missing essential environment variables: RABBITMQ_URL, SMU_API_URL, or SMU_X_CONTACTS_KEY")

def callback(ch, method, properties, body):
    """
    Callback triggered when a message is received from the queue.
    Performs a fire-and-forget HTTP POST to the SMU API.
    """
    try:
        # 1. Parse the incoming JSON message
        message = json.loads(body.decode('utf-8'))
        receiver = message.get("receiver")
        subject = message.get("subject")
        content = message.get("content")

        if not all([receiver, subject, content]):
            logging.warning(f"Invalid message format received: {message}")
            # The finally block will handle the basic_ack
            return

        logging.info(f"Processing notification for: {receiver}")

        # 2. Map the JSON to the SMU API's expected format
        payload = {
            "emailAddress": receiver,
            "emailSubject": subject,
            "emailBody": content
        }

        # 3. Add necessary Headers
        headers = {
            "Content-Type": "application/json",
            "X-Contacts-Key": SMU_X_CONTACTS_KEY
        }

        # 4. Trigger HTTP POST
        response = requests.post(SMU_API_URL, json=payload, headers=headers)
        
        if response.status_code in [200, 201, 202]:
            logging.info(f"Successfully notified {receiver}. Status: {response.status_code}")
            # Acknowledge the message since it was successful
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            logging.error(f"Failed to notify {receiver}. Status: {response.status_code}, Response: {response.text}")
            # Requeue the message: nack with requeue=True puts it back in the queue
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
