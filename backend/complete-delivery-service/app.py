import os, io, json, base64, requests
from flask import Flask, request, jsonify
import qrcode
import pika
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DELIVERY_URL = os.environ.get("DELIVERY_URL", "http://delivery-service:5000")
RIDER_URL    = os.environ.get("RIDER_URL",    "http://rider-service:5001")
ORDER_URL    = os.environ.get("ORDER_URL",    "https://personal-wi9fn0qz.outsystemscloud.com/Order_Service/rest/OrderAPI")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

def notify_patient(payload):
    params = pika.URLParameters(RABBITMQ_URL)
    conn   = pika.BlockingConnection(params)
    ch     = conn.channel()
    ch.exchange_declare(exchange="service_exchange", exchange_type="direct", durable=True)
    ch.queue_declare(queue="notification_queue", durable=True)
    ch.queue_bind(queue="notification_queue", exchange="service_exchange", routing_key="notification")
    ch.basic_publish(
        exchange="service_exchange",
        routing_key="notification",
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
    data = request.get_json(silent=True) or {}
    delivery_id = data.get("deliveryID")
    rider_id = data.get("riderID")
    if not delivery_id or not rider_id:
        return jsonify({"code": 400, "message": "deliveryID and riderID are required"}), 400

    try:
        # 1. Fetch delivery to get orderID and patient contact
        delivery_resp = requests.get(f"{DELIVERY_URL}/delivery/{delivery_id}", timeout=10)
        delivery_resp.raise_for_status()
        delivery = (delivery_resp.json() or {}).get("data", {})
        if not delivery:
            return jsonify({"code": 404, "message": "Delivery not found"}), 404
    except requests.exceptions.RequestException as e:
        return jsonify({"code": 502, "message": f"Failed to fetch delivery: {str(e)}"}), 502

    warnings = []

    # 2. Mark delivery as completed
    try:
        requests.put(f"{DELIVERY_URL}/delivery/{delivery_id}/delivered", timeout=10).raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"code": 502, "message": f"Failed to mark delivery complete: {str(e)}"}), 502

    # 3. Update order status to delivered (best effort; do not block rider release)
    try:
        requests.put(
            f"{ORDER_URL}/UpdateOrderStatus?OrderId={delivery['orderID']}&NewStatus=delivered",
            json={},
            timeout=10,
        ).raise_for_status()
    except requests.exceptions.RequestException as e:
        warnings.append(f"Order status update failed: {str(e)}")

    # 4. Free up the rider (critical)
    try:
        requests.put(f"{RIDER_URL}/rider/{rider_id}", json={"status": "available"}, timeout=10).raise_for_status()
    except requests.exceptions.RequestException as e:
        warnings.append(f"Rider status reset failed: {str(e)}")

    # 5. Notify patient via AMQP (best effort)
    try:
        notify_patient({
            "event_type": "order_delivered",
            "orderID": delivery.get("orderID"),
            "patientName": delivery.get("patientName"),
            "patientEmail": delivery.get("patientEmail"),
            "patientPhone": delivery.get("patientPhone"),
            "message": "Your medication has been delivered!",
        })
    except Exception as e:
        warnings.append(f"Patient notification failed: {str(e)}")

    return jsonify({"code": 200, "message": "Delivery completed", "warnings": warnings}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004, debug=True)
