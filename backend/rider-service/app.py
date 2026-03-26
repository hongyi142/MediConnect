from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import auth, credentials, firestore

app = Flask(__name__)
CORS(app)

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

DEFAULT_STAFF_PASSWORD = "NewStaff123!"


def now_utc():
    return datetime.now(timezone.utc)


def to_json(data):
    out = dict(data)
    for key in ["createdAt", "updatedAt"]:
        if out.get(key) and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    return out


def rider_doc_by_id(rider_id):
    direct = db.collection("Rider").document(rider_id).get()
    if direct.exists:
        return direct
    docs = db.collection("Rider").where("riderID", "==", rider_id).limit(1).stream()
    for doc in docs:
        return doc
    return None


def create_staff_user_doc(firebase_uid, role, name, linked_id, email):
    ref = db.collection("users").document(firebase_uid)
    snap = ref.get()
    payload = {
        "uid": firebase_uid,
        "role": role,
        "name": name,
        "linkedID": linked_id,
        "email": email,
        "updatedAt": now_utc(),
    }
    if not snap.exists:
        payload["createdAt"] = now_utc()
    ref.set(payload, merge=True)


def create_rider_record(data, create_auth=False):
    required = ["name", "phone"]
    if create_auth:
        required.append("email")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return None, jsonify({"code": 400, "error": f"Missing required fields: {', '.join(missing)}"}), 400

    firebase_uid = data.get("firebaseUID")
    auth_created = False

    try:
        if create_auth:
            user = auth.create_user(
                email=data["email"],
                password=DEFAULT_STAFF_PASSWORD,
                display_name=data["name"],
                disabled=False,
            )
            firebase_uid = user.uid
            auth_created = True

        doc_ref = db.collection("Rider").document()
        rider = {
            "riderID": doc_ref.id,
            "name": data["name"],
            "phone": data["phone"],
            "email": data.get("email"),
            "status": data.get("status", "available"),  # available | delivering
            "firebaseUID": firebase_uid,
            "createdAt": now_utc(),
            "updatedAt": now_utc(),
        }
        doc_ref.set(rider)

        if firebase_uid:
            create_staff_user_doc(
                firebase_uid=firebase_uid,
                role="rider",
                name=rider["name"],
                linked_id=rider["riderID"],
                email=rider.get("email"),
            )

        response = to_json(rider)
        if auth_created:
            response["defaultPassword"] = DEFAULT_STAFF_PASSWORD
        return response, None, None
    except Exception:
        if auth_created and firebase_uid:
            try:
                auth.delete_user(firebase_uid)
            except Exception:
                pass
        raise


@app.route("/rider", methods=["POST"])
def create_rider():
    data = request.get_json(silent=True) or {}
    create_auth = bool(data.get("createAuth", False))
    rider, err_resp, err_code = create_rider_record(data, create_auth=create_auth)
    if err_resp:
        return err_resp, err_code
    return jsonify({"code": 201, "data": rider}), 201


@app.route("/rider/register", methods=["POST"])
def register_rider():
    data = request.get_json(silent=True) or {}
    rider, err_resp, err_code = create_rider_record(data, create_auth=True)
    if err_resp:
        return err_resp, err_code
    return jsonify({"code": 201, "data": rider}), 201


@app.route("/rider/<rider_id>", methods=["GET"])
def get_rider(rider_id):
    doc = rider_doc_by_id(rider_id)
    if not doc:
        return jsonify({"code": 404, "error": "Rider not found"}), 404
    return jsonify({"code": 200, "data": to_json(doc.to_dict())})


@app.route("/rider/<rider_id>", methods=["PUT"])
def update_rider(rider_id):
    data = request.get_json(silent=True) or {}
    doc = rider_doc_by_id(rider_id)
    if not doc:
        return jsonify({"code": 404, "error": "Rider not found"}), 404
    data["updatedAt"] = now_utc()
    doc.reference.update(data)
    return jsonify({"code": 200, "message": "Updated"})


@app.route("/rider/free", methods=["GET"])
def get_free_riders():
    docs = db.collection("Rider").where("status", "==", "available").stream()
    riders = [to_json(d.to_dict()) for d in docs]
    return jsonify({"code": 200, "data": riders})


@app.route("/rider/list", methods=["GET"])
def list_riders():
    status = request.args.get("status")
    query = db.collection("Rider")
    if status:
        query = query.where("status", "==", status)
    docs = query.stream()
    riders = [to_json(d.to_dict()) for d in docs]
    return jsonify({"code": 200, "data": riders})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
