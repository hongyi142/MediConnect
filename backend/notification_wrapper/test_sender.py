import json
import pika


def send_test_message():
    # 1. Connect to RabbitMQ on localhost
    print("Connecting to RabbitMQ...")
    connection = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
    channel = connection.channel()

    # 2. Setup exchange and queue
    exchange_name = "service_exchange"
    queue_name = "notification_queue"
    routing_key = "notification"

    channel.exchange_declare(exchange=exchange_name, exchange_type="direct", durable=True)
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)

    # 3. Create JSON payload
    email_message = {
        "receiver": "testuser@gmail.com",
        "subject": "System Integration Test",
        "content": "Hello! This email message went through RabbitMQ.",
        "channel": "email",
    }
    sms_message = {
        "mobile": "92345678",
        "message": "Hello! This SMS message went through RabbitMQ.",
        "channel": "sms",
    }
    both_message = {
        "receiver": "testuser@gmail.com",
        "subject": "System Integration Test (Both)",
        "content": "This message was sent through email and SMS via RabbitMQ.",
        "mobile": "92345678",
        "message": "BOTH channel test.",
        "channel": "both",
    }

    # 4. publish messages
    for message in [both_message]:
        print(f"Publishing {message['channel']} test message...")
        channel.basic_publish(
            exchange=exchange_name,
            routing_key=routing_key,
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE),
        )

    print("Sent email/sms/both test messages to RabbitMQ!")

    # 5. Close connection
    connection.close()


if __name__ == "__main__":
    send_test_message()
