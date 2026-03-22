import os, threading, json, requests
from flask import Flask, request, jsonify
import pika
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DELIVERY_URL = os.environ.get("DELIVERY_URL", "http://delivery:5000")
RIDER_URL    = os.environ.get("RIDER_URL",    "http://rider:5001")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

# ── AMQP setup ─────────────────────────────────────────────────────────────
def get_channel():
    params = pika.URLParameters(RABBITMQ_URL)
    conn   = pika.BlockingConnection(params)
    return conn, conn.channel()

def broadcast_to_riders(delivery_data):
    """Publish delivery job to a fanout exchange so all Rider UIs receive it."""
    conn, ch = get_channel()
    ch.exchange_declare(exchange="delivery.available", exchange_type="fanout", durable=True)
    ch.basic_publish(
        exchange="delivery.available",
        routing_key="",
        body=json.dumps(delivery_data),
        properties=pika.BasicProperties(delivery_mode=2)
    )
    conn.close()

def notify_patient(payload):
    """Tell the Notification service that a rider was assigned."""
    conn, ch = get_channel()
    ch.queue_declare(queue="delivery.assigned", durable=True)
    ch.basic_publish(
        exchange="",
        routing_key="delivery.assigned",
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2)
    )
    conn.close()

def on_order_paid(ch, method, properties, body):
    """
    Triggered by P4 when payment succeeds.
    body contains: orderID, patientName, patientAddress, patientPhone, patientEmail, totalAmount
    """
    order = json.loads(body)
    print(f"[Assign Delivery] Received paid order: {order['orderID']}")

    # 1. Create a delivery record
    resp = requests.post(f"{DELIVERY_URL}/delivery", json={
        "orderID":        order["orderID"],
        "patientName":    order["patientName"],
        "patientAddress": order["patientAddress"],
        "patientPhone":   order["patientPhone"],
        "patientEmail":   order["patientEmail"],
    })
    delivery = resp.json()["data"]

    # 2. Broadcast to all Rider UIs
    broadcast_to_riders({
        "deliveryID":     delivery["deliveryID"],
        "patientAddress": delivery["patientAddress"],
        "orderID":        delivery["orderID"],
    })

    ch.basic_ack(delivery_tag=method.delivery_tag)

def start_amqp_listener():
    conn, ch = get_channel()
    ch.queue_declare(queue="order.paid", durable=True)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue="order.paid", on_message_callback=on_order_paid)
    print("[Assign Delivery] Listening on order.paid ...")
    ch.start_consuming()

# ── HTTP endpoints ──────────────────────────────────────────────────────────
@app.route("/accept-delivery", methods=["POST"])
def accept_delivery():
    """Rider accepts a delivery. Body: { deliveryID, riderID }"""
    data      = request.get_json()
    delivery_id = data["deliveryID"]
    rider_id    = data["riderID"]

    # Get rider details
    rider = requests.get(f"{RIDER_URL}/rider/{rider_id}").json()["data"]

    # Update delivery: assign rider
    requests.put(f"{DELIVERY_URL}/delivery/{delivery_id}", json={
        "riderID":   rider_id,
        "riderName": rider["name"],
        "status":    "assigned"
    })

    # Update rider status
    requests.put(f"{RIDER_URL}/rider/{rider_id}", json={"status": "delivering"})

    # Notify patient via AMQP → Notification service
    delivery = requests.get(f"{DELIVERY_URL}/delivery/{delivery_id}").json()["data"]
    notify_patient({
        "email":   delivery["patientEmail"],
        "phone":   delivery["patientPhone"],
        "message": f"Your rider {rider['name']} is on the way!"
    })

    return jsonify({"code": 200, "message": "Rider assigned"}), 200

if __name__ == "__main__":
    # Start AMQP listener in a background thread
    t = threading.Thread(target=start_amqp_listener, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5002, debug=False)