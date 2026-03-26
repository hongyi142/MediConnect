import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])


def call(method, url, **kwargs):
    try:
        resp = requests.request(method, url, timeout=10, **kwargs)
        return resp
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(str(exc)) from exc


@app.errorhandler(Exception)
def handle_exception(err):
    code = 503 if isinstance(err, RuntimeError) else getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "complete-consultation"})


@app.route("/available-medications")
def available_medications():
    inventory_url = os.environ.get("INVENTORY_SERVICE_URL", "http://inventory-service:5005").rstrip("/")
    resp = call("GET", f"{inventory_url}/inventory")
    resp.raise_for_status()
    meds = resp.json().get("medications", [])
    filtered = [m for m in meds if m.get("quantity", 0) > 0]
    return jsonify({"medications": filtered})


@app.route("/complete-consultation", methods=["POST"])
def complete_consultation():
    body = request.get_json(silent=True) or {}
    appt_id = body.get("apptID")
    patient_id = body.get("patientID")
    doctor_id = body.get("doctorID")
    notes = body.get("notes", "")
    medications = body.get("medications", [])
    issue_mc = bool(body.get("issueMC", False))

    consultation_url = os.environ.get("CONSULTATION_SERVICE_URL", "http://consultation-service:5004").rstrip("/")
    openai_url = os.environ.get("OPENAI_WRAPPER_URL", "http://openai-wrapper:5021").rstrip("/")
    inventory_url = os.environ.get("INVENTORY_SERVICE_URL", "http://inventory-service:5005").rstrip("/")
    order_url = os.environ.get("ORDER_SERVICE_URL", "https://personal-wi9fn0qz.outsystemscloud.com/Order_Service/rest/OrderAPI").rstrip("/")
    mc_url = os.environ.get("MC_SERVICE_URL", "http://mc-service:5010").rstrip("/")
    notification_url = os.environ.get("NOTIFICATION_WRAPPER_URL", "http://notification-wrapper:5011").rstrip("/")

    # Step 1
    notes_resp = call("PUT", f"{consultation_url}/consultation/{appt_id}/notes", json={"notes": notes})
    notes_resp.raise_for_status()

    # Step 2 (non-blocking)
    summary = ""
    try:
        summary_resp = call("POST", f"{openai_url}/openai/summarise-notes", json={"notes": notes})
        if summary_resp.ok:
            summary = summary_resp.json().get("summary", "")
    except Exception:
        summary = ""

    # Step 3 (deduct with compensation)
    deducted = []
    for med in medications:
        med_id = med.get("medicationID")
        qty = int(med.get("qty", 0))
        name = med.get("medicationName", med_id)
        resp = call("PUT", f"{inventory_url}/inventory/{med_id}/deduct", json={"qty": qty})
        if resp.status_code >= 400:
            for item in deducted:
                call(
                    "PUT",
                    f"{inventory_url}/inventory/{item['medicationID']}/deduct",
                    json={"qty": -int(item['qty'])},
                )
            return jsonify({"error": f"Stock for {name} was just taken."}), 409
        deducted.append(med)

    total_amount = sum(float(m.get("qty", 0)) * float(m.get("unitPrice", 0)) for m in medications)

    # Step 4
    order_resp = call(
        "POST",
        f"{order_url}/order",
        json={
            "apptID": appt_id,
            "patientID": patient_id,
            "doctorID": doctor_id,
            "totalAmount": total_amount,
            "status": "pending",
        },
    )
    order_resp.raise_for_status()
    order_data = order_resp.json()
    order_id = order_data.get("orderID")

    for med in medications:
        call(
            "POST",
            f"{order_url}/order/{order_id}/items",
            json={
                "inventoryID": med.get("medicationID"),
                "medicationName": med.get("medicationName"),
                "qty": med.get("qty"),
                "unitPrice": med.get("unitPrice"),
                "subtotal": float(med.get("qty", 0)) * float(med.get("unitPrice", 0)),
            },
        ).raise_for_status()

    # Step 5
    mc_issued = False
    mc_key = None
    mc_download = None
    if issue_mc:
        mc_payload = dict(body.get("mcDetails") or {})
        mc_payload["patientID"] = patient_id
        mc_resp = call("POST", f"{mc_url}/mc/generate", json=mc_payload)
        mc_resp.raise_for_status()
        mc_data = mc_resp.json()
        mc_issued = True
        mc_key = mc_data.get("mcKey")
        mc_download = mc_data.get("downloadUrl")

    # Step 6
    complete_resp = call(
        "PUT",
        f"{consultation_url}/consultation/{appt_id}/complete",
        json={"summary": summary, "mcIssued": mc_issued, "mcKey": mc_key},
    )
    complete_resp.raise_for_status()
    consultation = complete_resp.json()

    # Step 7 (best-effort notification, non-blocking)
    try:
        call(
            "POST",
            f"{notification_url}/notify/order-ready",
            json={
                "apptID": appt_id,
                "patientID": patient_id,
                "orderID": order_id,
                "totalAmount": total_amount,
                "mcIssued": mc_issued,
                "mcDownloadUrl": mc_download,
                "summary": summary,
            },
        )
    except Exception:
        pass

    return jsonify(
        {
            "message": "Consultation completed",
            "consultationID": consultation.get("consultationID"),
            "orderID": order_id,
            "totalAmount": total_amount,
            "aiSummary": summary,
            "mcIssued": mc_issued,
            "mcDownloadUrl": mc_download,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5014)
