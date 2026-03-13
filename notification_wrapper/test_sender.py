import json
import pika

def send_test_message():
    # 1. Connect to RabbitMQ on localhost
    print("Connecting to RabbitMQ...")
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()

    # 2. Setup exchange and queue
    exchange_name = 'service_exchange'
    queue_name = 'notification_queue'
    routing_key = 'notification'

    channel.exchange_declare(exchange=exchange_name, exchange_type='direct', durable=True)
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)

    # 3. Create JSON payload
    message = {
        "receiver": "hongyi.lee.2024@computing.smu.edu.sg",
        "subject": "System Integration Test",
        "content": "Hello! This message traveled through RabbitMQ and the SMU Notification API successfully."
    }
    
    # Convert JSON to string
    message_body = json.dumps(message)

    # 4. publish the message securely
    print(f"Publishing message to {exchange_name}...")
    channel.basic_publish(
        exchange=exchange_name,
        routing_key=routing_key,
        body=message_body,
        # delivery_mode=2 makes the message persistent (saved to disk so it survives restarts)
        properties=pika.BasicProperties(
            delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE
        )
    )

    print("Sent test message to RabbitMQ!")
    
    # 5. Close connection
    connection.close()

if __name__ == "__main__":
    send_test_message()
