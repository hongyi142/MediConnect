import os, io, json, base64, requests
from flask import Flask, request, jsonify
import qrcode
import pika
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DELIVERY_URL = os.environ.get("DELIVERY_URL", "http://delivery-service:5000")
RIDER_URL    = os.environ.get("RIDER_URL",    "http://rider-service:5001")
ORDER_URL    = os.environ.get("ORDER_URL",    "http://order:5003")  # P3's service
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

def notify_patient(payload):
    params = pika.URLParameters(RABBITMQ_URL)
    conn   = pika.BlockingConnection(params)
    ch     = conn.channel()
    ch.queue_declare(queue="delivery.completed", durable=True)
    ch.basic_publish(
        exchange="",
        routing_key="delivery.completed",
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2)
    )
    conn.close()

@app.route("/prepare-delivery", methods=["POST"])
def prepare_delivery():
    """
    Called by Patient UI when delivery is incoming.
    Body: { deliveryID, patientID }
    Returns: base64-encoded QR code image.
    """
    data        = request.get_json()
    delivery_id = data["deliveryID"]

    # Generate QR code encoding the deliveryID
    qr = qrcode.make(delivery_id)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return jsonify({"code": 200, "qrCode": qr_b64, "deliveryID": delivery_id})

@app.route("/complete-delivery", methods=["POST"])
def complete_delivery():
    """
    Called by Rider UI after scanning QR.
    Body: { deliveryID, riderID }
    """
    data        = request.get_json()
    delivery_id = data["deliveryID"]
    rider_id    = data["riderID"]

    # 1. Fetch delivery to get orderID and patient contact
    delivery = requests.get(f"{DELIVERY_URL}/delivery/{delivery_id}").json()["data"]

    # 2. Mark delivery as completed
    requests.put(f"{DELIVERY_URL}/delivery/{delivery_id}/delivered")

    # 3. Update order status to delivered (P3's Order service)
    requests.put(f"{ORDER_URL}/order/{delivery['orderID']}/status",
                 json={"status": "delivered"})

    # 4. Free up the rider
    requests.put(f"{RIDER_URL}/rider/{rider_id}", json={"status": "available"})

    # 5. Notify patient via AMQP
    notify_patient({
        "email":   delivery["patientEmail"],
        "phone":   delivery["patientPhone"],
        "message": "Your medication has been delivered!"
    })

    return jsonify({"code": 200, "message": "Delivery completed"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004, debug=True)
