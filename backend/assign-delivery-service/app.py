import os, threading, json, requests
from flask import Flask, request, jsonify
import pika
import googlemaps
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

DELIVERY_URL     = os.environ.get("DELIVERY_URL", "http://delivery-service:5000")
RIDER_URL        = os.environ.get("RIDER_URL",    "http://rider-service:5001")
RABBITMQ_URL     = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
GOOGLE_MAPS_KEY  = os.environ.get("GOOGLE_MAPS_API_KEY")

gmaps = googlemaps.Client(key=GOOGLE_MAPS_KEY) if GOOGLE_MAPS_KEY else None

# â”€â”€ Geolocation helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def find_nearest_rider(patient_address, available_riders, radius_km=1):
    """
    Given a patient address and list of available riders (each with lat/lng),
    returns the single nearest rider within radius_km, or None if none found.
    """
    # Filter riders that have location set
    riders_with_location = [
        r for r in available_riders
        if r.get("latitude") and r.get("longitude")
    ]

    if not riders_with_location:
        print("[Assign Delivery] No riders with location data found")
        return None

    # Build origins list from rider coordinates
    origins = [
        (r["latitude"], r["longitude"])
        for r in riders_with_location
    ]

    if not gmaps:
        print("[Assign Delivery] GOOGLE_MAPS_API_KEY missing, skipping nearest rider logic")
        return None

    # Call Google Maps Distance Matrix API
    result = gmaps.distance_matrix(
        origins=origins,
        destinations=[patient_address],
        mode="driving",
        units="metric"
    )

    nearest_rider  = None
    nearest_metres = float("inf")

    for i, row in enumerate(result["rows"]):
        element = row["elements"][0]

        # Skip if no route found
        if element["status"] != "OK":
            continue

        distance_m = element["distance"]["value"]  # in metres
        distance_km = distance_m / 1000

        print(f"[Assign Delivery] Rider {riders_with_location[i]['name']} "
              f"is {distance_km:.2f}km away")

        if distance_km <= radius_km and distance_m < nearest_metres:
            nearest_metres = distance_m
            nearest_rider  = riders_with_location[i]

    if nearest_rider:
        print(f"[Assign Delivery] Nearest rider: {nearest_rider['name']} "
              f"({nearest_metres/1000:.2f}km)")
    else:
        print(f"[Assign Delivery] No riders within {radius_km}km")

    return nearest_rider


def geocode_address(address):
    if not gmaps or not address:
        return None, None
    try:
        results = gmaps.geocode(address)
        if not results:
            return None, None
        loc = results[0].get("geometry", {}).get("location", {})
        lat = loc.get("lat")
        lng = loc.get("lng")
        if lat is None or lng is None:
            return None, None
        return float(lat), float(lng)
    except Exception:
        return None, None

# â”€â”€ AMQP setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def get_channel():
    params = pika.URLParameters(RABBITMQ_URL)
    conn   = pika.BlockingConnection(params)
    return conn, conn.channel()

def broadcast_to_riders(delivery_data):
    """Fallback â€” broadcast to all riders if no nearby rider found."""
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
    conn, ch = get_channel()
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


def assign_delivery_to_rider(delivery_id, rider_id):
    rider_resp = requests.get(f"{RIDER_URL}/rider/{rider_id}")
    if rider_resp.status_code >= 400:
        return None, "Rider not found", 404
    rider = rider_resp.json().get("data", {})
    if (rider.get("status") or "").lower() != "available":
        return None, "Rider already has an active delivery", 409

    claim_resp = requests.put(
        f"{RIDER_URL}/rider/{rider_id}",
        json={"status": "delivering", "expectedStatus": "available"},
    )
    if claim_resp.status_code >= 400:
        return None, "Rider already has an active delivery", 409

    assign_resp = requests.put(
        f"{DELIVERY_URL}/delivery/{delivery_id}",
        json={
            "riderID": rider_id,
            "riderName": rider.get("name"),
            "status": "assigned",
        },
    )
    if assign_resp.status_code >= 400:
        try:
            requests.put(f"{RIDER_URL}/rider/{rider_id}", json={"status": "available"})
        except Exception:
            pass
        msg = "Delivery assignment failed"
        try:
            msg = assign_resp.json().get("message", msg)
        except Exception:
            pass
        return None, msg, assign_resp.status_code

    return rider, None, 200

def on_order_paid(ch, method, properties, body):
    order = json.loads(body)
    print(f"[Assign Delivery] Received paid order: {order['orderID']}")

    # 1. Create delivery record
    patient_lat, patient_lng = geocode_address(order["patientAddress"])
    resp = requests.post(f"{DELIVERY_URL}/delivery", json={
        "orderID":        order["orderID"],
        "patientName":    order["patientName"],
        "patientID":      order.get("patientID"),
        "patientAddress": order["patientAddress"],
        "patientPhone":   order.get("patientPhone"),
        "patientEmail":   order["patientEmail"],
        "patientLat":     patient_lat,
        "patientLng":     patient_lng,
    })
    delivery = resp.json()["data"]

    # 2. Get all available riders
    riders_resp      = requests.get(f"{RIDER_URL}/rider/free")
    available_riders = riders_resp.json().get("data", [])

    # 3. Find nearest rider within 1km
    nearest = find_nearest_rider(order["patientAddress"], available_riders, radius_km=1)

    if nearest:
        # Auto-assign nearest rider
        print(f"[Assign Delivery] Auto-assigning {nearest['name']}")
        rider, err, _ = assign_delivery_to_rider(delivery["deliveryID"], nearest["riderID"])
        if rider:
            notify_patient({
                "event_type": "rider_assigned",
                "orderID": order.get("orderID"),
                "patientName": order.get("patientName"),
                "patientEmail": order.get("patientEmail"),
                "patientPhone": order.get("patientPhone"),
                "patientID": order.get("patientID"),
                "riderName": rider.get("name"),
                "message": f"Rider {rider['name']} has been assigned to your delivery!",
            })
        else:
            print(f"[Assign Delivery] Auto-assignment failed: {err}; broadcasting instead")
            broadcast_to_riders({
                "deliveryID":     delivery["deliveryID"],
                "patientAddress": delivery["patientAddress"],
                "orderID":        delivery["orderID"],
            })
    else:
        # Fallback â€” broadcast to all riders manually
        print("[Assign Delivery] No nearby rider â€” broadcasting to all")
        broadcast_to_riders({
            "deliveryID":     delivery["deliveryID"],
            "patientAddress": delivery["patientAddress"],
            "orderID":        delivery["orderID"],
        })

    ch.basic_ack(delivery_tag=method.delivery_tag)

def start_amqp_listener():
    import time
    while True:
        try:
            conn, ch = get_channel()
            exchange_name = "service_exchange"
            queue_name = "delivery_queue"
            routing_key = "delivery.assign"
            arguments = {
                "x-dead-letter-exchange": "refund_exchange",
                "x-message-ttl": 10000
            }
            ch.exchange_declare(exchange=exchange_name, exchange_type="direct", durable=True)
            ch.queue_declare(queue=queue_name, durable=True, arguments=arguments)
            ch.queue_bind(queue=queue_name, exchange=exchange_name, routing_key=routing_key)
            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(queue=queue_name, on_message_callback=on_order_paid)
            print("[Assign Delivery] Listening on delivery_queue ...")
            ch.start_consuming()
        except Exception as e:
            print(f"[Assign Delivery] AMQP error: {e} â€” retrying in 5s")
            time.sleep(5)

# â”€â”€ HTTP endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route("/accept-delivery", methods=["POST"])
def accept_delivery():
    """Manual fallback - rider accepts from broadcast."""
    data = request.get_json() or {}
    delivery_id = data.get("deliveryID")
    rider_id = data.get("riderID")
    if not delivery_id or not rider_id:
        return jsonify({"code": 400, "message": "deliveryID and riderID are required"}), 400

    rider, err, err_code = assign_delivery_to_rider(delivery_id, rider_id)
    if err:
        code = err_code if isinstance(err_code, int) and err_code >= 100 else 409
        return jsonify({"code": code, "message": err}), code

    delivery_resp = requests.get(f"{DELIVERY_URL}/delivery/{delivery_id}")
    delivery = delivery_resp.json().get("data", {}) if delivery_resp.status_code < 400 else {}
    notify_patient({
        "event_type": "rider_assigned",
        "orderID": delivery.get("orderID"),
        "patientName": delivery.get("patientName"),
        "patientEmail": delivery.get("patientEmail"),
        "patientPhone": delivery.get("patientPhone"),
        "patientID": delivery.get("patientID"),
        "riderName": rider.get("name"),
        "message": f"Your rider {rider['name']} is on the way!",
    })

    return jsonify({"code": 200, "message": "Rider assigned"}), 200

@app.route("/nearest-rider", methods=["POST"])
def nearest_rider():
    """
    Test endpoint â€” given an address, returns nearest available rider.
    Body: { "address": "123 Orchard Road, Singapore" }
    """
    data    = request.get_json()
    address = data.get("address")

    riders_resp      = requests.get(f"{RIDER_URL}/rider/free")
    available_riders = riders_resp.json().get("data", [])

    nearest = find_nearest_rider(address, available_riders, radius_km=1)

    if nearest:
        return jsonify({"code": 200, "data": nearest})
    return jsonify({"code": 404, "message": "No riders within 1km"}), 404

if __name__ == "__main__":
    t = threading.Thread(target=start_amqp_listener, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5002, debug=False)
