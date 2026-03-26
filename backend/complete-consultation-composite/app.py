import os
import zlib
from urllib.parse import urlencode

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


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def _extract_order_id(payload):
    if isinstance(payload, dict):
        for key in ["orderID", "orderId", "OrderID", "OrderId", "id", "Id"]:
            val = payload.get(key)
            if val is not None and str(val).strip():
                return str(val)
        nested = payload.get("data")
        if nested is not None:
            nested_id = _extract_order_id(nested)
            if nested_id:
                return nested_id
    if isinstance(payload, list):
        for row in payload:
            nested_id = _extract_order_id(row)
            if nested_id:
                return nested_id
    return None


def _to_medication_selection(medications):
    items = []
    for med in medications or []:
        med_id = med.get("medicationID") or med.get("MedicationId")
        qty = int(med.get("qty", med.get("Quantity", 0)) or 0)
        unit_price = float(med.get("unitPrice", med.get("UnitPrice", 0)) or 0)
        if med_id and qty > 0:
            items.append(
                {
                    "MedicationId": str(med_id),
                    "Quantity": qty,
                    "UnitPrice": unit_price,
                }
            )
    return items


def _to_order_patient_key(patient_id):
    raw = str(patient_id or "").strip()
    if raw.isdigit():
        return raw
    # OrderAPI GetOrdersByPatient expects an integer-compatible PatientId.
    return str((zlib.crc32(raw.encode("utf-8")) % 900000000) + 100000000)


def _find_recent_order_id(order_url, patient_id, appt_id, doctor_id):
    try:
        query = urlencode({"PatientId": patient_id})
        resp = call("GET", f"{order_url}/GetOrdersByPatient?{query}")
        if resp.status_code >= 400:
            return None
        payload = _safe_json(resp)
        if not isinstance(payload, list):
            return None
        for row in sorted(payload, key=lambda r: int(r.get("Id", 0) or 0), reverse=True):
            if (
                str(row.get("ApptId", "")) == str(appt_id)
                and str(row.get("DoctorId", "")) == str(doctor_id)
            ):
                if row.get("Id") is not None:
                    return str(row.get("Id"))
        if payload and payload[0].get("Id") is not None:
            return str(payload[0].get("Id"))
    except Exception:
        return None
    return None


def create_order(order_url, appt_id, patient_id, doctor_id, total_amount, medications):
    order_patient_id = _to_order_patient_key(patient_id)
    query = urlencode(
        {
            "ApptId": appt_id,
            "PatientId": order_patient_id,
            "DoctorId": doctor_id,
        }
    )
    create_order_url = f"{order_url}/CreateOrder?{query}"
    # Create empty order first; add items via AddOrderItem afterwards.
    # This avoids CreateOrder hard-failing when external inventory lookup fails.
    create_payload = {"Items": []}
    candidates = [
        ("POST", create_order_url, create_payload),
    ]
    if "outsystemscloud.com" not in order_url:
        candidates.append(
            (
                "POST",
                f"{order_url}/order",
                {
                    "apptID": appt_id,
                    "patientID": patient_id,
                    "doctorID": doctor_id,
                    "totalAmount": total_amount,
                    "status": "pending",
                },
            )
        )

    last_resp = None
    for method, url, payload in candidates:
        resp = call(method, url, json=payload) if payload is not None else call(method, url)
        if resp.status_code < 400:
            body = _safe_json(resp)
            order_id = _extract_order_id(body)
            if not order_id:
                txt = (resp.text or "").strip().strip('"')
                if txt:
                    order_id = txt
            if not order_id and url == create_order_url:
                order_id = _find_recent_order_id(order_url, order_patient_id, appt_id, doctor_id)
            if order_id:
                return order_id
        last_resp = resp

    if last_resp is not None:
        snippet = (last_resp.text or "")[:300]
        raise RuntimeError(f"Failed to create order. HTTP {last_resp.status_code}: {snippet}")
    raise RuntimeError("Failed to create order.")


def push_order_item(order_url, order_id, med):
    qty = int(med.get("qty", med.get("Quantity", 0)) or 0)
    unit_price = float(med.get("unitPrice", med.get("UnitPrice", 0)) or 0)
    add_item_url = f"{order_url}/AddOrderItem?{urlencode({'OrderId': order_id})}"
    item_payload_outsystems = {
        "MedicationId": med.get("medicationID"),
        "Quantity": qty,
        "UnitPrice": unit_price,
    }
    item_payload_local = {
        "inventoryID": med.get("medicationID"),
        "medicationName": med.get("medicationName"),
        "qty": qty,
        "unitPrice": unit_price,
        "subtotal": qty * unit_price,
    }
    candidates = [
        ("POST", add_item_url, item_payload_outsystems),
        (
            "POST",
            f"{order_url}/order/{order_id}/items",
            item_payload_local,
        ),
        ("POST", f"{order_url}/CreateOrderItem", item_payload_outsystems),
    ]

    for method, url, payload in candidates:
        resp = call(method, url, json=payload) if payload is not None else call(method, url)
        if resp.status_code < 400:
            return True
    return False


def has_order_items(order_url, order_id):
    try:
        query = urlencode({"OrderId": order_id})
        resp = call("GET", f"{order_url}/GetItemsByOrder?{query}")
        if resp.status_code >= 400:
            return False
        payload = _safe_json(resp)
        return isinstance(payload, list) and len(payload) > 0
    except Exception:
        return False


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
    payload = resp.json()
    meds = payload.get("medications") or payload.get("data") or []
    filtered = [m for m in meds if float(m.get("quantity", m.get("Quantity", 0)) or 0) > 0]
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
    order_id = create_order(order_url, appt_id, patient_id, doctor_id, total_amount, medications)
    item_sync_failed = False
    if not has_order_items(order_url, order_id):
        for med in medications:
            ok = push_order_item(order_url, order_id, med)
            if not ok:
                item_sync_failed = True

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
            "orderItemsSynced": not item_sync_failed,
            "aiSummary": summary,
            "mcIssued": mc_issued,
            "mcDownloadUrl": mc_download,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5014)
