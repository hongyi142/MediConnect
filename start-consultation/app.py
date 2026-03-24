import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


def req(method, url, **kwargs):
    try:
        return requests.request(method, url, timeout=10, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(str(exc)) from exc


@app.errorhandler(Exception)
def handle_exception(err):
    code = 503 if isinstance(err, RuntimeError) else getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "start-consultation"})


@app.route("/start-consultation", methods=["POST"])
def start_consultation():
    body = request.get_json(silent=True) or {}
    appt_id = body.get("apptID")
    patient_id = body.get("patientID")
    doctor_id = body.get("doctorID")
    if not appt_id or not patient_id or not doctor_id:
        return jsonify({"error": "apptID, patientID, doctorID are required"}), 400

    appointment_url = os.environ.get("APPOINTMENT_SERVICE_URL", "http://mock-service:5099").rstrip("/")
    twilio_url = os.environ.get("TWILIO_WRAPPER_URL", "http://twilio-wrapper:5020").rstrip("/")
    consultation_url = os.environ.get("CONSULTATION_SERVICE_URL", "http://consultation-service:5004").rstrip("/")

    appointment_resp = req("GET", f"{appointment_url}/appointment/{appt_id}")
    appointment_resp.raise_for_status()
    appointment = appointment_resp.json()
    if appointment.get("status") != "confirmed":
        return jsonify({"error": "Appointment is not confirmed"}), 400

    room_name = f"consult-{appt_id}"
    room_resp = req("POST", f"{twilio_url}/twilio/room", json={"roomName": room_name})
    if room_resp.status_code >= 400:
        return jsonify({"error": "Failed to create video room"}), 500
    room_resp.raise_for_status()
    room = room_resp.json()

    patient_token_resp = req(
        "POST",
        f"{twilio_url}/twilio/token",
        json={"identity": f"patient-{patient_id}", "roomName": room_name},
    )
    patient_token_resp.raise_for_status()

    doctor_token_resp = req(
        "POST",
        f"{twilio_url}/twilio/token",
        json={"identity": f"doctor-{doctor_id}", "roomName": room_name},
    )
    doctor_token_resp.raise_for_status()

    consult_resp = req("POST", f"{consultation_url}/consultation", json={"apptID": appt_id, "roomName": room_name})
    consult_resp.raise_for_status()
    consult = consult_resp.json()

    return jsonify(
        {
            "consultationID": consult.get("consultationID"),
            "apptID": appt_id,
            "roomName": room_name,
            "patientToken": patient_token_resp.json().get("token"),
            "doctorToken": doctor_token_resp.json().get("token"),
            "twilioRoomSid": room.get("roomSid"),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5013)