import os
from flask import Flask, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

CONFIG_KEYS = [
    "PATIENT_SERVICE_URL", "DOCTOR_SERVICE_URL", "APPT_SERVICE_URL",
    "CONSULTATION_SERVICE_URL", "ORDER_SERVICE_URL", "INV_SERVICE_URL",
    "PAYMENT_SERVICE_URL", "DELIVERY_SERVICE_URL", "RIDER_SERVICE_URL",
    "MC_SERVICE_URL", "TWILIO_WRAPPER_URL", "OPENAI_WRAPPER_URL",
    "PAYMENT_WRAPPER_URL", "START_CONSULTATION_URL",
    "COMPLETE_CONSULTATION_URL", "BOOK_APPOINTMENT_URL",
    "PROCESS_PAYMENT_URL", "ASSIGN_DELIVERY_URL", "COMPLETE_DELIVERY_URL",
    "NOTIFICATION_WRAPPER_URL", "SSE_SERVICE_URL",
    "FIREBASE_API_KEY", "FIREBASE_AUTH_DOMAIN", "FIREBASE_PROJECT_ID",
    "FIREBASE_STORAGE_BUCKET", "FIREBASE_MESSAGING_SENDER_ID",
    "FIREBASE_APP_ID"
]


@app.context_processor
def inject_config():
    return {key: os.environ.get(key, "") for key in CONFIG_KEYS}


# ── Landing ──
@app.route("/")
def index():
    return render_template("index.html")


# ── Auth ──
@app.route("/auth/login")
def auth_login():
    return render_template("auth/login.html")


@app.route("/auth/signup")
def auth_signup():
    return render_template("auth/signup.html")


@app.route("/auth/doctor-signup")
def auth_doctor_signup():
    return render_template("auth/doctor_signup.html")


# ── Patient ──
@app.route("/patient/dashboard")
def patient_dashboard():
    return render_template("patient/dashboard.html")


@app.route("/patient/symptom-checker")
def patient_symptom_checker():
    return render_template("patient/symptom_checker.html")


@app.route("/patient/book")
def patient_book():
    return render_template("patient/book_appointment.html")


@app.route("/patient/appointments")
def patient_appointments():
    return render_template("patient/appointments.html")


@app.route("/patient/join")
def patient_join():
    return render_template("patient/join.html")


@app.route("/patient/consultation")
def patient_consultation():
    return render_template("patient/consultation.html")


@app.route("/patient/payment")
def patient_payment():
    return render_template("patient/payment.html")


@app.route("/patient/order-status")
def patient_order_status():
    return render_template("patient/order_status.html")


@app.route("/patient/history")
def patient_history():
    return render_template("patient/history.html")


# ── Doctor ──
@app.route("/doctor/dashboard")
def doctor_dashboard():
    return render_template("doctor/dashboard.html")


@app.route("/doctor/staff")
def doctor_staff():
    return render_template("doctor/staff_management.html")


@app.route("/doctor/schedule")
def doctor_schedule():
    return render_template("doctor/schedule.html")


@app.route("/doctor/join")
def doctor_join():
    return render_template("doctor/join.html")


@app.route("/doctor/consultation")
def doctor_consultation():
    return render_template("doctor/consultation.html")


@app.route("/doctor/prescription")
def doctor_prescription():
    return render_template("doctor/prescription.html")


@app.route("/doctor/mc")
def doctor_mc():
    return render_template("doctor/mc.html")


@app.route("/doctor/inventory")
def doctor_inventory():
    return render_template("doctor/inventory.html")


# ── Shared ──
@app.route("/account/settings")
def account_settings():
    return render_template("account/settings.html")


# ── Rider ──
@app.route("/rider/dashboard")
def rider_dashboard():
    return render_template("rider/dashboard.html")


@app.route("/rider/delivery")
def rider_delivery():
    return render_template("rider/delivery.html")


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_RUN_PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
