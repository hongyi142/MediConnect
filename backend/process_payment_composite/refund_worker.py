import os
import json
import requests
import pika
from dotenv import load_dotenv

load_dotenv()

PAYMENT_WRAPPER_URL = os.getenv("PAYMENT_WRAPPER_URL", "http://127.0.0.1:5001")
PAYMENT_ATOMIC_URL = os.getenv("PAYMENT_ATOMIC_URL", "http://127.0.0.1:5000")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://localhost/")

def start_worker():
    print("Initializing Refund Worker (DLX Consumer)...")
    try:
        parameters = pika.URLParameters(RABBITMQ_URL)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()

        # Declare the DLX exchange
        refund_exchange = 'refund_exchange'
        channel.exchange_declare(exchange=refund_exchange, exchange_type='direct', durable=True)

        # Declare the refund queue and bind it to the DLX exchange
        queue_name = 'refund_queue'
        channel.queue_declare(queue=queue_name, durable=True)
        # Using the original routing key ('delivery.assign') because the generic DLX forwards it intact.
        channel.queue_bind(queue=queue_name, exchange=refund_exchange, routing_key='delivery.assign')

        print("Refund Worker started. Listening to 'refund_queue' queue. To exit press CTRL+C")

        def callback(ch, method, properties, body):
            try:
                msg_payload = json.loads(body)
                order_id = msg_payload.get('orderID')
                session_id = msg_payload.get('session_id')
                document_id = msg_payload.get('documentID')
                patient_name = msg_payload.get('patientName')
                patient_email = msg_payload.get('patientEmail')
                amount = msg_payload.get('amount')
                
                print(f"Dead letter received for order {order_id}. Initiating refund saga...")

                if not session_id or not document_id:
                    print(f"Error: Missing session_id or documentID in payload: {msg_payload}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                # Make API call to Payment Wrapper to process Stripe refund
                wrapper_payload = {"session_id": session_id}
                wrapper_resp = requests.post(f"{PAYMENT_WRAPPER_URL}/payment/refund", json=wrapper_payload)
                wrapper_resp.raise_for_status()
                
                # Make API call to Payment Atomic to gracefully update Firestore ledger
                atomic_payload = {"status": "refunded"}
                atomic_resp = requests.put(f"{PAYMENT_ATOMIC_URL}/payment/{document_id}", json=atomic_payload)
                atomic_resp.raise_for_status()

                # --- NEW: Publish async event to notification_queue ---
                if patient_email and patient_name and amount:
                    notification_payload = {
                        "event_type": "payment_refunded",
                        "patientEmail": patient_email,
                        "patientName": patient_name,
                        "orderID": order_id,
                        "amount": amount
                    }
                    
                    ch.basic_publish(
                        exchange='service_exchange', 
                        routing_key='notification',
                        body=json.dumps(notification_payload),
                        properties=pika.BasicProperties(
                            delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE
                        )
                    )
                # -----------------------------------------------------------------

                print(f"Saga Reversal Complete: Order {order_id} refunded.")
                
            except requests.exceptions.RequestException as e:
                print(f"API Error during refund sequence: {e}")
            except Exception as e:
                print(f"Refund sequence failed with unexpected error: {e}")
            
            # Acknowledge the message so it is dropped from the DLX queue regardless of final outcome, avoiding infinite loop processing
            ch.basic_ack(delivery_tag=method.delivery_tag)

        channel.basic_consume(queue=queue_name, on_message_callback=callback)
        channel.start_consuming()

    except pika.exceptions.AMQPConnectionError as err:
        print(f"Failed to connect to RabbitMQ: {err}")
    except KeyboardInterrupt:
        print("Refund Worker interrupted by user. Shutting down...")
        if 'connection' in locals() and connection.is_open:
            connection.close()

if __name__ == "__main__":
    start_worker()
