import os

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

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
    return jsonify({"status": "ok", "service": "book-appointment"})


def get_service_urls():
    patient_url = os.environ.get("PATIENT_SERVICE_URL", "http://patient-service:5030").rstrip("/")
    doctor_url = os.environ.get("DOCTOR_SERVICE_URL", "http://doctor-service:5031").rstrip("/")
    appointment_url = os.environ.get("APPOINTMENT_SERVICE_URL", "http://appointment-service:5032").rstrip("/")
    return patient_url, doctor_url, appointment_url


def has_conflict(appointment_url, doctor_id, date_time):
    conflict_resp = req("GET", f"{appointment_url}/appointment/conflict", params={"doctorID": doctor_id, "dateTime": date_time})
    conflict_resp.raise_for_status()
    return bool(conflict_resp.json().get("conflict"))


@app.route("/book-appointment", methods=["POST"])
def book_appointment():
    body = request.get_json(silent=True) or {}
    patient_id = body.get("patientID")
    date_time = body.get("dateTime")
    preferred_doctor_id = body.get("doctorID")
    specialization = body.get("specialisation")

    if not patient_id or not date_time:
        return jsonify({"error": "patientID and dateTime are required"}), 400

    patient_url, doctor_url, appointment_url = get_service_urls()

    patient_resp = req("GET", f"{patient_url}/patient/{patient_id}")
    if patient_resp.status_code == 404:
        return jsonify({"error": "Patient not found"}), 404
    patient_resp.raise_for_status()

    selected_doctor = None

    if preferred_doctor_id:
        doctor_resp = req("GET", f"{doctor_url}/doctor/{preferred_doctor_id}")
        if doctor_resp.status_code == 404:
            return jsonify({"error": "Preferred doctor not found"}), 404
        doctor_resp.raise_for_status()

        doctor = doctor_resp.json()
        if doctor.get("status") != "available":
            return jsonify({"error": "Preferred doctor is not available"}), 409
        if has_conflict(appointment_url, preferred_doctor_id, date_time):
            return jsonify({"error": "Preferred doctor already has an overlapping appointment"}), 409
        selected_doctor = doctor
    else:
        available_resp = req("GET", f"{doctor_url}/doctor/available", params={"specialisation": specialization} if specialization else None)
        available_resp.raise_for_status()
        doctors = available_resp.json().get("doctors", [])

        for doctor in doctors:
            doc_id = doctor.get("doctorID")
            if doc_id and not has_conflict(appointment_url, doc_id, date_time):
                selected_doctor = doctor
                break

        if not selected_doctor:
            return jsonify({"error": "No available doctor for this timeslot"}), 409

    appointment_resp = req(
        "POST",
        f"{appointment_url}/appointment",
        json={
            "patientID": patient_id,
            "doctorID": selected_doctor.get("doctorID"),
            "dateTime": date_time,
            "status": "pending",
        },
    )
    appointment_resp.raise_for_status()
    appointment = appointment_resp.json()

    return jsonify(
        {
            "message": "Appointment booked and pending doctor confirmation",
            "appointmentID": appointment.get("appointmentID"),
            "status": appointment.get("status"),
            "patientID": appointment.get("patientID"),
            "doctorID": appointment.get("doctorID"),
            "dateTime": appointment.get("dateTime"),
        }
    ), 201


@app.route("/book-appointment/respond", methods=["POST"])
def respond_to_appointment():
    body = request.get_json(silent=True) or {}
    appointment_id = body.get("appointmentID")
    status = body.get("status")
    cancellation_reason = body.get("cancellationReason")

    if not appointment_id or status not in ["confirmed", "cancelled"]:
        return jsonify({"error": "appointmentID and status (confirmed/cancelled) are required"}), 400

    _, _, appointment_url = get_service_urls()
    payload = {"status": status}
    if status == "cancelled" and cancellation_reason:
        payload["cancellationReason"] = cancellation_reason

    resp = req("PUT", f"{appointment_url}/appointment/{appointment_id}", json=payload)
    if resp.status_code == 404:
        return jsonify({"error": "Appointment not found"}), 404
    resp.raise_for_status()

    return jsonify({"message": f"Appointment {status}", "appointment": resp.json()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5033)
