from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv  
from datetime import datetime, timezone
import os, json

load_dotenv()

app = Flask(__name__)

cred_dict = json.loads(os.environ.get("FIREBASE_CREDENTIALS"))
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

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

@app.route("/delivery/<delivery_id>", methods=["GET"])
def get_delivery(delivery_id):
    doc = db.collection("deliveries").document(delivery_id).get()
    if not doc.exists:
        return jsonify({"code": 404, "message": "Not found"}), 404
    return jsonify({"code": 200, "data": doc.to_dict()})

@app.route("/delivery/<delivery_id>", methods=["PUT"])
def update_delivery(delivery_id):
    data = request.get_json()
    db.collection("deliveries").document(delivery_id).update(data)
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