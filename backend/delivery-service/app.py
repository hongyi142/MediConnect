from flask import Flask, request, jsonify
from flask_cors import CORS #REMOVE ONCE TESTING IS DONE
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app) #REMOVE ONCE TESIING IS DONE

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()


def to_json(data):
    out = dict(data)
    for key in ["createdAt", "completedAt"]:
        if out.get(key) and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    return out

@app.route("/delivery", methods=["POST"])
def create_delivery():
    data = request.get_json()
    # Required fields: orderID, patientName, patientAddress, patientPhone, patientEmail
    doc_ref = db.collection("deliveries").document()
    delivery = {
        "deliveryID": doc_ref.id,
        "orderID": data["orderID"],
        "riderID": None,
        "riderName": None,
        "patientName": data["patientName"],
        "patientAddress": data["patientAddress"],
        "patientPhone": data["patientPhone"],
        "patientEmail": data["patientEmail"],
        "status": "pending",   # pending → assigned → completed
        "createdAt": datetime.now(timezone.utc).isoformat()
    }
    doc_ref.set(delivery)
    return jsonify({"code": 201, "data": delivery}), 201


@app.route("/delivery", methods=["GET"])
def list_deliveries():
    docs = db.collection("deliveries").stream()
    deliveries = [to_json(doc.to_dict()) for doc in docs]
    return jsonify({"code": 200, "data": deliveries})

@app.route("/delivery/<delivery_id>", methods=["GET"])
def get_delivery(delivery_id):
    doc = db.collection("deliveries").document(delivery_id).get()
    if not doc.exists:
        return jsonify({"code": 404, "message": "Not found"}), 404
    return jsonify({"code": 200, "data": to_json(doc.to_dict())})

@app.route("/delivery/<delivery_id>", methods=["PUT"])
def update_delivery(delivery_id):
    data = request.get_json(silent=True) or {}
    ref = db.collection("deliveries").document(delivery_id)
    snap = ref.get()
    if not snap.exists:
        return jsonify({"code": 404, "message": "Not found"}), 404

    current = snap.to_dict() or {}
    current_status = (current.get("status") or "").lower()
    incoming_status = (data.get("status") or current.get("status") or "").lower()
    current_rider = current.get("riderID")
    incoming_rider = data.get("riderID", current_rider)

    # Prevent mutating completed deliveries.
    if current_status in ["completed", "delivered"] and any(k in data for k in ["status", "riderID", "riderName"]):
        return jsonify({"code": 409, "message": "Delivery is already completed"}), 409

    # Prevent rider reassignment once a rider is already set.
    if current_rider and incoming_rider and incoming_rider != current_rider:
        return jsonify({"code": 409, "message": "Delivery already assigned to another rider"}), 409

    # Restrict assignment transition to pending/ready (or idempotent same rider assignment).
    if incoming_status == "assigned" and current_status not in ["pending", "ready", "assigned"]:
        return jsonify({"code": 409, "message": f"Cannot assign delivery from status '{current_status}'"}), 409

    ref.update(data)
    return jsonify({"code": 200, "message": "Updated"})

@app.route("/delivery/<delivery_id>/delivered", methods=["PUT"])
def mark_delivered(delivery_id):
    db.collection("deliveries").document(delivery_id).update({
        "status": "completed",
        "completedAt": datetime.now(timezone.utc).isoformat()
    })
    return jsonify({"code": 200, "message": "Marked as delivered"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
