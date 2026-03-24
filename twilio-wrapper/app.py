import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VideoGrant
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


def get_client():
    return Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))


@app.errorhandler(Exception)
def handle_exception(err):
    http_status = getattr(err, "status", None)
    if not isinstance(http_status, int) or http_status < 100 or http_status > 599:
        code_attr = getattr(err, "code", None)
        http_status = code_attr if isinstance(code_attr, int) and 100 <= code_attr <= 599 else 500
    body = {"error": str(err)}
    twilio_code = getattr(err, "code", None)
    if isinstance(twilio_code, int) and twilio_code > 599:
        body["twilioCode"] = twilio_code
    return jsonify(body), http_status


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "twilio-wrapper"})


@app.route("/twilio/token", methods=["POST"])
def create_token():
    body = request.get_json(silent=True) or {}
    identity = body.get("identity")
    room_name = body.get("roomName")
    if not identity or not room_name:
        return jsonify({"error": "identity and roomName are required"}), 400

    token = AccessToken(
        os.environ.get("TWILIO_ACCOUNT_SID"),
        os.environ.get("TWILIO_API_KEY"),
        os.environ.get("TWILIO_API_SECRET"),
        identity=identity,
        ttl=3600,
    )
    token.add_grant(VideoGrant(room=room_name))
    jwt_token = token.to_jwt()
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode("utf-8")
    return jsonify({"token": jwt_token, "identity": identity, "roomName": room_name})


@app.route("/twilio/room", methods=["POST"])
def create_room():
    body = request.get_json(silent=True) or {}
    room_name = body.get("roomName")
    if not room_name:
        return jsonify({"error": "roomName is required"}), 400

    try:
        room = get_client().video.v1.rooms.create(unique_name=room_name, type="group")
    except TwilioRestException as exc:
        # Make room creation idempotent for repeated join attempts on same appointment.
        if getattr(exc, "code", None) == 53113:
            room = get_client().video.v1.rooms(room_name).fetch()
        else:
            raise
    return jsonify({"roomName": room.unique_name, "roomSid": room.sid, "status": room.status})


@app.route("/twilio/room/<room_name>")
def get_room(room_name):
    room = get_client().video.v1.rooms(room_name).fetch()
    return jsonify({"roomName": room.unique_name, "roomSid": room.sid, "status": room.status})


@app.route("/twilio/room/<room_name>", methods=["DELETE"])
def end_room(room_name):
    get_client().video.v1.rooms(room_name).update(status="completed")
    return jsonify({"message": "Room ended", "roomName": room_name})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5020)
