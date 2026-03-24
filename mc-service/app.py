import base64
import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from weasyprint import HTML

load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:8080", "http://frontend:8080", "*"])

S3_HEADERS = {
    "Content-Type": "application/json",
    "X-Contacts-Key": "4e46111f-f4a9-443b-bc63-cd0d52437c04",
}


def s3_post(path, payload):
    base = os.environ.get(
        "S3_BASE_URL",
        "https://smuedu-dev.outsystemsenterprise.com/SMULab_AmazonS3/rest/AmazonS3",
    ).rstrip("/")
    return requests.post(f"{base}/{path.lstrip('/')}", json=payload, headers=S3_HEADERS, timeout=10)


def render_mc_html(data):
    issued = datetime.utcnow().strftime("%d %B %Y")
    return f"""
    <html><body style='font-family: Arial, sans-serif; padding: 40px;'>
    <div style='text-align:center;'>
      <div style='font-size:28px;font-weight:700;color:#0d9488;'>MediConnect Teleclinic</div>
      <div style='font-size:13px;color:#6b7280;'>Online Medical Consultation</div>
      <hr style='border:0;border-top:2px solid #0d9488;' />
    </div>
    <div style='text-align:center;margin-top:24px;'>
      <div style='font-size:20px;font-weight:700;letter-spacing:3px;'>MEDICAL CERTIFICATE</div>
      <div style='font-size:12px;color:#6b7280;font-style:italic;'>This is to certify that:</div>
    </div>
    <div style='background:#f3f4f6;border-radius:8px;padding:16px;margin-top:20px;'>
      <div><span style='font-size:11px;color:#6b7280;'>Full Name:</span> <strong>{data['patientName']}</strong></div>
      <div><span style='font-size:11px;color:#6b7280;'>NRIC:</span> <strong>{data['patientNRIC']}</strong></div>
      <div><span style='font-size:11px;color:#6b7280;'>Diagnosis:</span> <strong>{data['diagnosis']}</strong></div>
    </div>
    <div style='margin-top:20px;font-size:13px;'>
      <div>is certified unfit for work/school for {data['days']} day(s).</div>
      <div>Period: {data['startDate']} - {data['endDate']}</div>
    </div>
    <div style='margin-top:24px;font-size:13px;'>
      <div>Attending Physician: Dr. {data['doctorName']}</div>
      <div>Issued by: MediConnect Teleclinic</div>
      <div>Date Issued: {issued}</div>
    </div>
    <div style='margin-top:40px;border-top:2px solid #0d9488;padding-top:12px;font-size:10px;color:#6b7280;text-align:center;'>
      This MC was issued via teleconsultation on the MediConnect platform.
      This document is digitally generated and valid without a physical signature.
    </div>
    </body></html>
    """


@app.errorhandler(Exception)
def handle_exception(err):
    code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), code


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "mc-service"})


@app.route("/mc/generate", methods=["POST"])
def generate_mc():
    body = request.get_json(silent=True) or {}
    required = ["patientID", "patientName", "patientNRIC", "doctorName", "diagnosis", "startDate", "endDate", "days"]
    missing = [key for key in required if key not in body]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    pdf_bytes = HTML(string=render_mc_html(body)).write_pdf()
    file_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    file_name = f"MC_{body['patientID']}_{body['startDate']}.pdf"
    folder = os.environ.get("S3_FOLDER", "mediconnect")
    subfolder = os.environ.get("S3_SUBFOLDER", "medical-certificates")

    upload = s3_post(
        "UploadFile",
        {
            "folderName": folder,
            "subFolderName": subfolder,
            "fileName": file_name,
            "file": file_b64,
            "override": True,
        },
    )
    upload.raise_for_status()
    key = upload.json().get("key")

    url_resp = s3_post(
        "FetchFileUrl",
        {"folderName": folder, "subFolderName": subfolder, "key": key},
    )
    url_resp.raise_for_status()
    return jsonify({"mcKey": key, "downloadUrl": url_resp.json().get("url"), "fileName": file_name})


@app.route("/mc/<mc_key>")
def get_mc_url(mc_key):
    folder = os.environ.get("S3_FOLDER", "mediconnect")
    subfolder = os.environ.get("S3_SUBFOLDER", "medical-certificates")
    resp = s3_post("FetchFileUrl", {"folderName": folder, "subFolderName": subfolder, "key": mc_key})
    resp.raise_for_status()
    return jsonify({"downloadUrl": resp.json().get("url")})


@app.route("/mc/<mc_key>", methods=["DELETE"])
def delete_mc(mc_key):
    folder = os.environ.get("S3_FOLDER", "mediconnect")
    subfolder = os.environ.get("S3_SUBFOLDER", "medical-certificates")
    resp = s3_post("DeleteFile", {"folderName": folder, "subFolderName": subfolder, "key": mc_key})
    resp.raise_for_status()
    return jsonify({"message": "MC deleted", "key": mc_key})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010)