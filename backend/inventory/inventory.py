import base64
import os
import time
from typing import Any, Dict, Optional, Tuple

import firebase_admin
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from firebase_admin import credentials, firestore

load_dotenv()

app = Flask(__name__)

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

S3_WRAPPER_URL = os.environ.get("S3_WRAPPER_URL", "http://amazon-s3-wrapper:5020").rstrip("/")
S3_FOLDER = os.environ.get("S3_FOLDER", "mediconnect")
S3_SUBFOLDER = os.environ.get("S3_SUBFOLDER", "medication-images")
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def s3_post(path: str, payload: Dict[str, Any]) -> requests.Response:
    return requests.post(f"{S3_WRAPPER_URL}/{path.lstrip('/')}", json=payload, timeout=15)


def fetch_s3_url(file_key: Optional[str]) -> Optional[str]:
    if not file_key:
        return None
    resp = s3_post(
        "FetchFileUrl",
        {"folderName": S3_FOLDER, "subFolderName": S3_SUBFOLDER, "key": file_key},
    )
    resp.raise_for_status()
    return (resp.json() or {}).get("url")


def delete_s3_file(file_key: Optional[str]) -> None:
    if not file_key:
        return
    try:
        resp = s3_post(
            "DeleteFile",
            {"folderName": S3_FOLDER, "subFolderName": S3_SUBFOLDER, "key": file_key},
        )
        resp.raise_for_status()
    except Exception:
        # Best-effort cleanup only.
        pass


def _guess_extension(mime_type: str) -> str:
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }
    return mapping.get((mime_type or "").lower(), "png")


def upload_image_to_s3(file_obj, medication_id: str) -> str:
    if not file_obj or not getattr(file_obj, "filename", ""):
        raise ValueError("Image file is required.")

    mime_type = (file_obj.mimetype or "").lower()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("Unsupported image type. Use JPG, PNG or WEBP.")

    blob = file_obj.read()
    if not blob:
        raise ValueError("Uploaded image is empty.")

    file_name = f"medication_{medication_id}_{int(time.time())}.{_guess_extension(mime_type)}"
    file_b64 = base64.b64encode(blob).decode("utf-8")
    upload_resp = s3_post(
        "UploadFile",
        {
            "folderName": S3_FOLDER,
            "subFolderName": S3_SUBFOLDER,
            "fileName": file_name,
            "file": file_b64,
            "override": True,
        },
    )
    upload_resp.raise_for_status()
    key = (upload_resp.json() or {}).get("key")
    if not key:
        raise RuntimeError("S3 upload succeeded but no key was returned.")
    return key


def parse_number(raw: Any, cast_type):
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    return cast_type(raw)


def parse_payload() -> Tuple[Dict[str, Any], Optional[Any]]:
    content_type = (request.content_type or "").lower()
    if "multipart/form-data" in content_type:
        return {
            "name": request.form.get("name"),
            "description": request.form.get("description"),
            "quantity": request.form.get("quantity"),
            "price": request.form.get("price"),
        }, request.files.get("image")
    return request.get_json(silent=True) or {}, None


def enrich_item(doc_id: str, raw_item: Dict[str, Any], include_image_url: bool = True) -> Dict[str, Any]:
    item = dict(raw_item or {})
    if not item.get("medicationID"):
        item["medicationID"] = doc_id
    qty = item.get("quantity", item.get("Quantity", 0))
    item["quantity"] = qty
    item["Quantity"] = qty

    if include_image_url:
        file_key = item.get("fileKey")
        try:
            item["imageUrl"] = fetch_s3_url(file_key) if file_key else None
        except Exception:
            item["imageUrl"] = None
    return item


def bool_query_param(name: str, default: bool = True) -> bool:
    val = request.args.get(name)
    if val is None:
        return default
    return str(val).strip().lower() not in {"0", "false", "no", "off"}


@app.route("/inventory", methods=["GET"])
def get_inventory():
    try:
        include_image_url = bool_query_param("includeImageUrl", default=True)
        docs = db.collection("Inventory").stream()
        inventory_list = [enrich_item(doc.id, doc.to_dict() or {}, include_image_url=include_image_url) for doc in docs]
        return jsonify({"code": 200, "data": inventory_list, "medications": inventory_list})
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500


@app.route("/inventory/<string:medication_id>", methods=["GET"])
def get_one_item(medication_id):
    try:
        include_image_url = bool_query_param("includeImageUrl", default=True)
        item_ref = db.collection("Inventory").document(medication_id)
        item = item_ref.get()

        if not item.exists:
            return jsonify({"code": 404, "message": "Medication not found"}), 404

        firestore_data = item.to_dict() or {}
        payload = enrich_item(medication_id, firestore_data, include_image_url=include_image_url)
        return jsonify({"code": 200, "data": payload}), 200
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500


@app.route("/inventory", methods=["POST"])
def add_new_medication():
    doc_ref = None
    try:
        data, image_file = parse_payload()
        name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        quantity = parse_number(data.get("quantity"), int)
        price = parse_number(data.get("price"), float)

        if not name:
            return jsonify({"code": 400, "message": "name is required."}), 400
        if quantity is None or price is None:
            return jsonify({"code": 400, "message": "quantity and price are required."}), 400

        doc_ref = db.collection("Inventory").document()
        medication_id = doc_ref.id
        new_item = {
            "medicationID": medication_id,
            "name": name,
            "description": description,
            "quantity": quantity,
            "price": price,
            "fileKey": None,
        }
        doc_ref.set(new_item)

        if image_file and image_file.filename:
            key = upload_image_to_s3(image_file, medication_id)
            doc_ref.update({"fileKey": key})
            new_item["fileKey"] = key
            new_item["imageUrl"] = fetch_s3_url(key)
        else:
            new_item["imageUrl"] = None

        return jsonify(
            {
                "code": 201,
                "message": "New medication added.",
                "medicationID": medication_id,
                "fileKey": new_item.get("fileKey"),
                "imageUrl": new_item.get("imageUrl"),
            }
        ), 201
    except ValueError as e:
        if doc_ref is not None:
            try:
                doc_ref.delete()
            except Exception:
                pass
        return jsonify({"code": 400, "message": str(e)}), 400
    except requests.RequestException as e:
        if doc_ref is not None:
            try:
                doc_ref.delete()
            except Exception:
                pass
        return jsonify({"code": 502, "message": f"Image upload failed: {str(e)}"}), 502
    except Exception as e:
        if doc_ref is not None:
            try:
                doc_ref.delete()
            except Exception:
                pass
        return jsonify({"code": 500, "message": str(e)}), 500


@app.route("/inventory/<string:medication_id>", methods=["PUT"])
def restock(medication_id):
    try:
        data, image_file = parse_payload()
        item_ref = db.collection("Inventory").document(medication_id)
        item = item_ref.get()

        if not item.exists:
            return jsonify({"code": 404, "message": "Medication not found"}), 404

        current = item.to_dict() or {}
        update_data: Dict[str, Any] = {}

        if data.get("quantity") is not None:
            quantity_delta = parse_number(data.get("quantity"), int)
            if quantity_delta is None:
                return jsonify({"code": 400, "message": "quantity must be a valid integer."}), 400
            current_stock = int(current.get("quantity", current.get("Quantity", 0)) or 0)
            new_stock = current_stock + quantity_delta
            if new_stock < 0:
                return jsonify({"code": 400, "message": "Stock cannot be negative."}), 400
            update_data["quantity"] = new_stock

        if data.get("price") is not None:
            update_data["price"] = parse_number(data.get("price"), float)

        if data.get("name") is not None:
            update_data["name"] = str(data.get("name") or "").strip()

        if data.get("description") is not None:
            update_data["description"] = str(data.get("description") or "").strip()

        old_key = current.get("fileKey")
        new_key = None
        if image_file and image_file.filename:
            new_key = upload_image_to_s3(image_file, medication_id)
            update_data["fileKey"] = new_key

        if not update_data:
            return jsonify({"code": 400, "message": "No valid fields to update."}), 400

        item_ref.update(update_data)
        if new_key and old_key and old_key != new_key:
            delete_s3_file(old_key)

        latest = item_ref.get().to_dict() or {}
        response_data = enrich_item(medication_id, latest, include_image_url=True)
        return jsonify({"code": 200, "message": "Inventory updated successfully", "data": response_data})
    except ValueError as e:
        return jsonify({"code": 400, "message": str(e)}), 400
    except requests.RequestException as e:
        return jsonify({"code": 502, "message": f"Image upload failed: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500


@app.route("/inventory/<string:medication_id>/image", methods=["PUT"])
def update_medication_image(medication_id):
    try:
        image_file = request.files.get("image")
        if not image_file or not image_file.filename:
            return jsonify({"code": 400, "message": "image file is required."}), 400

        item_ref = db.collection("Inventory").document(medication_id)
        snap = item_ref.get()
        if not snap.exists:
            return jsonify({"code": 404, "message": "Medication not found"}), 404

        current = snap.to_dict() or {}
        old_key = current.get("fileKey")
        new_key = upload_image_to_s3(image_file, medication_id)
        item_ref.update({"fileKey": new_key})
        if old_key and old_key != new_key:
            delete_s3_file(old_key)

        image_url = fetch_s3_url(new_key)
        return jsonify(
            {
                "code": 200,
                "message": "Medication image updated.",
                "medicationID": medication_id,
                "fileKey": new_key,
                "imageUrl": image_url,
            }
        )
    except ValueError as e:
        return jsonify({"code": 400, "message": str(e)}), 400
    except requests.RequestException as e:
        return jsonify({"code": 502, "message": f"Image upload failed: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500


@app.route("/inventory/<string:medication_id>/deduct", methods=["PUT"])
def deduct_stock(medication_id):
    try:
        data = request.get_json(silent=True) or {}
        quantity_to_deduct = data.get("qty", data.get("quantity"))
        if quantity_to_deduct is None:
            return jsonify({"code": 400, "message": "qty or quantity is required."}), 400
        quantity_to_deduct = int(quantity_to_deduct)
        if quantity_to_deduct <= 0:
            return jsonify({"code": 400, "message": "qty must be greater than 0."}), 400

        item_ref = db.collection("Inventory").document(medication_id)
        item = item_ref.get()

        if not item.exists:
            return jsonify({"code": 404, "message": "Medication not found."}), 404

        current_data = item.to_dict() or {}
        current_stock = int(current_data.get("quantity", current_data.get("Quantity", 0)) or 0)

        if current_stock >= quantity_to_deduct:
            new_stock = current_stock - quantity_to_deduct
            item_ref.update({"quantity": new_stock})
            return jsonify({"code": 200, "message": "Stock deducted."}), 200

        return jsonify({"code": 400, "message": "Insufficient stock."}), 400
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005, debug=True)
