import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
import googlemaps

load_dotenv()

app = Flask(__name__)
CORS(app)

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY) if GOOGLE_MAPS_API_KEY else None


def _client_ready():
    return gmaps is not None


@app.get("/health")
def health():
    return jsonify(
        {
            "service": "distance-matrix-wrapper",
            "google_maps_configured": _client_ready(),
        }
    ), 200


@app.post("/geocode")
def geocode():
    body = request.get_json(silent=True) or {}
    address = str(body.get("address") or "").strip()
    if not address:
        return jsonify({"error": "address is required"}), 400

    if not _client_ready():
        return jsonify({"error": "GOOGLE_MAPS_API_KEY is missing"}), 503

    try:
        results = gmaps.geocode(address)
        if not results:
            return jsonify({"error": "address could not be geocoded"}), 404
        location = results[0].get("geometry", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is None or lng is None:
            return jsonify({"error": "address could not be geocoded"}), 404
        return jsonify({"lat": float(lat), "lng": float(lng)}), 200
    except Exception as exc:
        return jsonify({"error": "geocode upstream failed", "details": str(exc)}), 502


@app.post("/distance-matrix")
def distance_matrix():
    body = request.get_json(silent=True) or {}
    origins_raw = body.get("origins") or []
    destination = str(body.get("destination_address") or body.get("destination") or "").strip()
    mode = str(body.get("mode") or "driving")
    units = str(body.get("units") or "metric")

    if not isinstance(origins_raw, list) or not origins_raw:
        return jsonify({"error": "origins must be a non-empty list"}), 400
    if not destination:
        return jsonify({"error": "destination_address is required"}), 400
    if not _client_ready():
        return jsonify({"error": "GOOGLE_MAPS_API_KEY is missing"}), 503

    origins = []
    for item in origins_raw:
        if isinstance(item, dict):
            lat = item.get("latitude")
            lng = item.get("longitude")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            lat, lng = item[0], item[1]
        else:
            continue
        if lat is None or lng is None:
            continue
        origins.append((float(lat), float(lng)))

    if not origins:
        return jsonify({"error": "no valid origins with latitude/longitude"}), 400

    try:
        result = gmaps.distance_matrix(
            origins=origins,
            destinations=[destination],
            mode=mode,
            units=units,
        )
        return jsonify(
            {
                "rows": result.get("rows", []),
                "destination_addresses": result.get("destination_addresses", []),
                "origin_addresses": result.get("origin_addresses", []),
            }
        ), 200
    except Exception as exc:
        return jsonify({"error": "distance matrix upstream failed", "details": str(exc)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5063, debug=False)
