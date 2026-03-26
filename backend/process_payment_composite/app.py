import os
import requests
import pika
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment configuration
load_dotenv()

PAYMENT_WRAPPER_URL = os.getenv("PAYMENT_WRAPPER_URL", "http://127.0.0.1:5001")
PAYMENT_ATOMIC_URL = os.getenv("PAYMENT_ATOMIC_URL", "http://127.0.0.1:5000")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://localhost/")
ORDER_URL = os.getenv("ORDER_URL", "https://personal-wi9fn0qz.outsystemscloud.com/Order_Service/rest/OrderAPI")
PATIENT_URL = os.getenv("PATIENT_URL", "http://patient-service:5030")

app = Flask(__name__)
CORS(app)

@app.route('/api/checkout', methods=['POST'])
def initiate_checkout():
    """
    Phase 1: Initiate Payment
    Orchestrates the creation of a Stripe Checkout Session via the Wrapper,
    and stores the intent atomically in the Database.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        order_id = data.get('orderID')
        patient_id = data.get('patientID')

        if not all([order_id, patient_id]):
            return jsonify({"error": "Missing required fields: orderID, patientID"}), 400

        # Step 0: Fetch Order Details from External Outsystems API
        try:
            order_response = requests.get(f"{ORDER_URL}/GetOrderDetails?OrderId={order_id}", timeout=10)
            if order_response.status_code == 404:
                return jsonify({"error": f"Order {order_id} not found"}), 404
            order_response.raise_for_status()
            
            order_data = order_response.json()
            total_amount = order_data.get("TotalAmount")
            
            if total_amount is None:
                return jsonify({"error": "Order TotalAmount is missing from Response"}), 502

            # Convert to cents
            amount = int(float(total_amount) * 100)
            item_name = f"Medical Order #{order_id}"
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Failed communicating with Order Service: {str(e)}"}), 503

        # Step 1: Call Payment Wrapper to create Stripe Session
        wrapper_payload = {
            "order_id": order_id,
            "item_name": item_name,
            "amount": amount
        }
        
        wrapper_response = requests.post(f"{PAYMENT_WRAPPER_URL}/payment/checkout", json=wrapper_payload, timeout=10)
        wrapper_response.raise_for_status()
        
        wrapper_data = wrapper_response.json()
        checkout_url = wrapper_data.get("checkout_url")
        session_id = wrapper_data.get("session_id")

        if not checkout_url or not session_id:
            return jsonify({"error": "Payment Wrapper returned malformed data"}), 502

        # Step 2: Call Payment Atomic to record the pending transaction
        atomic_payload = {
            "orderID": order_id,
            "stripeIntentID": session_id,
            "amount": amount
        }
        
        atomic_response = requests.post(f"{PAYMENT_ATOMIC_URL}/payment/create", json=atomic_payload, timeout=10)
        atomic_response.raise_for_status()
        
        atomic_data = atomic_response.json()
        document_id = atomic_data.get("documentID")

        if not document_id:
            return jsonify({"error": "Payment Atomic Service returned malformed data"}), 502

        # Step 3: Return Orchestrated Response to Client
        return jsonify({
            "checkout_url": checkout_url,
            "session_id": session_id,
            "documentID": document_id
        }), 201

    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": "Failed communicating with foundational microservices",
            "details": str(e)
        }), 502
    except Exception as e:
        return jsonify({
            "error": "Internal server error during checkout initiation",
            "details": str(e)
        }), 500


@app.route('/api/verify', methods=['POST'])
def verify_and_handoff():
    """
    Phase 2: Verify & Handoff
    Verifies the payment with Stripe, updates the Atomic Ledger, and
    triggers the delivery queue in RabbitMQ.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        session_id = data.get('session_id')
        document_id = data.get('documentID')
        order_id = data.get('orderID')
        patient_id = data.get('patientID')

        if not all([session_id, document_id, order_id, patient_id]):
            return jsonify({"error": "Missing required fields for verification"}), 400

        # Step 0A: Fetch Patient Data Dynamically
        try:
            patient_response = requests.get(f"{PATIENT_URL}/patient/{patient_id}", timeout=10)
            if patient_response.status_code == 404:
                return jsonify({"error": f"Patient {patient_id} not found"}), 404
            patient_response.raise_for_status()
            
            patient_data = patient_response.json()
            patient_name = patient_data.get("name", "Unknown")
            patient_address = patient_data.get("address", "Unknown")
            patient_email = patient_data.get("email", "Unknown")
            patient_phone = patient_data.get("phone", "Unknown")
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Failed communicating with Patient Service: {str(e)}"}), 503

        # Step 0B: Fetch Order Data dynamically to get accurate amount for MQ handoff
        try:
            order_response = requests.get(f"{ORDER_URL}/GetOrderDetails?OrderId={order_id}", timeout=10)
            if order_response.status_code == 404:
                return jsonify({"error": f"Order {order_id} not found"}), 404
            order_response.raise_for_status()
            
            order_data = order_response.json()
            total_amount = order_data.get("TotalAmount", 0)
            amount = int(float(total_amount) * 100)
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Failed communicating with Order Service: {str(e)}"}), 503

        # Step 1: Verify Payment Status via Payment Wrapper
        status_response = requests.get(f"{PAYMENT_WRAPPER_URL}/payment/status/{session_id}", timeout=10)
        status_response.raise_for_status()
        
        payment_status = status_response.json().get("payment_status")
        
        if payment_status != 'paid':
            return jsonify({
                "error": "Payment is not complete",
                "current_status": payment_status
            }), 400

        # Step 2: Update Ledger via Payment Atomic
        update_payload = {"status": "paid"}
        update_response = requests.put(f"{PAYMENT_ATOMIC_URL}/payment/{document_id}", json=update_payload, timeout=10)
        update_response.raise_for_status()

        # Step 3: RabbitMQ Handoff
        try:
            params = pika.URLParameters(RABBITMQ_URL)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()

            exchange_name = 'service_exchange'
            refund_exchange = 'refund_exchange'
            queue_name = 'delivery_queue'
            routing_key = 'delivery.assign'

            # Ensure exchanges exist
            channel.exchange_declare(exchange=exchange_name, exchange_type='direct', durable=True)
            channel.exchange_declare(exchange=refund_exchange, exchange_type='direct', durable=True)

            # Declare the delivery_queue with DLX arguments
            arguments = {
                'x-dead-letter-exchange': refund_exchange,
                'x-message-ttl': 10000  # 10 seconds for testing
            }
            channel.queue_declare(queue=queue_name, durable=True, arguments=arguments)
            channel.queue_bind(queue=queue_name, exchange=exchange_name, routing_key=routing_key)

            msg_payload = {
                "orderID": order_id,
                "session_id": session_id,
                "documentID": document_id,
                "patientID": patient_id,
                "patientName": patient_name,
                "patientAddress": patient_address,
                "patientEmail": patient_email,
                "patientPhone": patient_phone,
                "amount": amount,
                "status": "payment_verified"
            }

            channel.basic_publish(
                exchange=exchange_name,
                routing_key=routing_key,
                body=json.dumps(msg_payload),
                properties=pika.BasicProperties(
                    delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE
                )
            )

            # --- NEW: Publish async event to notification_queue ---
            notification_payload = {
                "event_type": "payment_successful",
                "patientEmail": patient_email,
                "patientName": patient_name,
                "orderID": order_id
            }
            
            channel.basic_publish(
                exchange='service_exchange', 
                routing_key='notification',
                body=json.dumps(notification_payload),
                properties=pika.BasicProperties(
                    delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE
                )
            )
            # -----------------------------------------------------------------
            
            connection.close()
        except pika.exceptions.AMQPError as e:
            # If RabbitMQ fails, we should alert the client that payment succeeded but handoff failed
            return jsonify({
                "error": "Payment verified but failed to enqueue delivery handoff",
                "details": str(e)
            }), 502

        # Return final success
        return jsonify({
            "message": "Payment verified and delivery assignment triggered successfully."
        }), 200

    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": "Failed communicating with foundational microservices",
            "details": str(e)
        }), 502
    except Exception as e:
        return jsonify({
            "error": "Internal server error during verification",
            "details": str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.getenv('FLASK_RUN_PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=True)
