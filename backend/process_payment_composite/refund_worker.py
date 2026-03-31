import os
import json
import requests
import pika
import time
from dotenv import load_dotenv

load_dotenv()

PAYMENT_WRAPPER_URL = os.getenv("PAYMENT_WRAPPER_URL", "http://127.0.0.1:5001")
PAYMENT_ATOMIC_URL = os.getenv("PAYMENT_ATOMIC_URL", "http://127.0.0.1:5000")
DELIVERY_SERVICE_URL = os.getenv("DELIVERY_SERVICE_URL", "http://127.0.0.1:5000")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
ORDER_URL = os.getenv("ORDER_URL", "https://personal-wi9fn0qz.outsystemscloud.com/Order_Service/rest/OrderAPI")

def start_worker():
    print("Initializing Refund Worker (The Auditor)...")
    while True:
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
                    patient_id = msg_payload.get('patientID')
                    amount = msg_payload.get('amount')
                    
                    print(f"Dead letter received for order {order_id}. Auditing status...")

                    # --- AUDIT STEP: Check if a rider has already accepted the job ---
                    try:
                        # Fetch all deliveries and filter by orderID
                        delivery_resp = requests.get(f"{DELIVERY_SERVICE_URL}/delivery", timeout=10)
                        delivery_resp.raise_for_status()
                        deliveries = delivery_resp.json().get("data", [])
                        
                        # Find the delivery record for this order
                        delivery_record = next((d for d in deliveries if str(d.get("orderID")) == str(order_id)), None)
                        
                        if delivery_record:
                            status = (delivery_record.get("status") or "pending").lower()
                            print(f"Current delivery status for order {order_id}: {status}")
                            
                            # If rider already assigned or delivery completed, SKIP refund
                            if status in ["assigned", "completed", "delivered", "shipping"]:
                                print(f"Audit Result: Order {order_id} already has a rider assigned. Skipping refund.")
                                ch.basic_ack(delivery_tag=method.delivery_tag)
                                return
                        else:
                            print(f"Warning: No delivery record found for order {order_id}. Proceeding with refund.")
                    except Exception as audit_err:
                        print(f"Audit Check Failed: {audit_err}. Proceeding with safety-first refund.")

                    if not session_id or not document_id:
                        print(f"Error: Missing session_id or documentID in payload: {msg_payload}")
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                        return

                    # --- REFUND SAGA ---
                    print(f"Initiating refund saga for order {order_id}...")
                    wrapper_payload = {"session_id": session_id}
                    wrapper_resp = requests.post(f"{PAYMENT_WRAPPER_URL}/payment/refund", json=wrapper_payload)
                    wrapper_resp.raise_for_status()
                    
                    # Make API call to Payment Atomic to gracefully update Firestore ledger
                    atomic_payload = {"status": "refunded"}
                    atomic_resp = requests.put(f"{PAYMENT_ATOMIC_URL}/payment/{document_id}", json=atomic_payload)
                    atomic_resp.raise_for_status()

                    # --- NEW: Update external OutSystems Order ---
                    try:
                        order_update = requests.put(f"{ORDER_URL}/UpdateOrderStatus?OrderId={order_id}&NewStatus=refunded", json={}, timeout=10)
                        order_update.raise_for_status()
                    except requests.exceptions.RequestException as e:
                        print(f"Warning: Failed to update OutSystems Order status: {e}")

                    # --- NEW: Publish async event to notification_queue ---
                    if patient_email and patient_name and amount:
                        notification_payload = {
                            "event_type": "payment_refunded",
                            "patientEmail": patient_email,
                            "patientName": patient_name,
                            "patientID": patient_id,
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

        except (pika.exceptions.AMQPConnectionError, pika.exceptions.AMQPChannelError) as err:
            print(f"Failed to connect to RabbitMQ: {err}. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            print("Refund Worker interrupted by user. Shutting down...")
            if 'connection' in locals() and connection.is_open:
                connection.close()
            break
        except Exception as e:
            print(f"Unexpected error in worker: {e}. Retrying in 5 seconds...")
            time.sleep(5)

if __name__ == "__main__":
    start_worker()
