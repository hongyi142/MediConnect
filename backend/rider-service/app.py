from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

app = Flask(__name__)

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route("/rider", methods=["POST"])
def create_rider():
    data = request.get_json()
    doc_ref = db.collection("riders").document()
    rider = {
        "riderID": doc_ref.id,
        "name": data["name"],
        "phone": data["phone"],
        "status": "available"  # available | delivering
    }
    doc_ref.set(rider)
    return jsonify({"code": 201, "data": rider}), 201

@app.route("/rider/<rider_id>", methods=["GET"])
def get_rider(rider_id):
    doc = db.collection("riders").document(rider_id).get()
    if not doc.exists:
        return jsonify({"code": 404}), 404
    return jsonify({"code": 200, "data": doc.to_dict()})

@app.route("/rider/<rider_id>", methods=["PUT"])
def update_rider(rider_id):
    data = request.get_json()
    db.collection("riders").document(rider_id).update(data)
    return jsonify({"code": 200, "message": "Updated"})

@app.route("/rider/free", methods=["GET"])
def get_free_riders():
    docs = db.collection("riders").where("status", "==", "available").stream()
    riders = [d.to_dict() for d in docs]
    return jsonify({"code": 200, "data": riders})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)