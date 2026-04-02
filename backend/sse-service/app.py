"""
SSE (Server-Sent Events) push notification service.

Clients connect via GET /sse/stream?userID=<linkedID> and receive real-time
events. Other services push events via POST /sse/notify (HTTP) or by publishing
to the RabbitMQ `sse_exchange` topic exchange with routing key `notify.<userID>`.
"""
import json
import os
import queue
import threading
import time

import pika
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["*"])

# In-memory registry: userID → list of threading.Queue (one per open browser tab)
_clients: dict = {}
_clients_lock = threading.Lock()

HEARTBEAT_INTERVAL = 25  # seconds — keeps proxies/browsers from timing out


# ── Client registry helpers ────────────────────────────────────────────────

def _register(user_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=100)
    with _clients_lock:
        _clients.setdefault(user_id, []).append(q)
    return q


def _unregister(user_id: str, q: queue.Queue) -> None:
    with _clients_lock:
        bucket = _clients.get(user_id)
        if bucket:
            try:
                bucket.remove(q)
            except ValueError:
                pass
            if not bucket:
                del _clients[user_id]


def _push(user_id: str, event: str, data: dict) -> int:
    """Push an event to all open connections for a user. Returns number of queues pushed to."""
    with _clients_lock:
        queues = list(_clients.get(user_id, []))
    pushed = 0
    for q in queues:
        try:
            q.put_nowait({"event": event, "data": data})
            pushed += 1
        except queue.Full:
            pass
    return pushed


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/sse/stream")
def sse_stream():
    """Browser connects here to receive push events."""
    user_id = request.args.get("userID", "").strip()
    if not user_id:
        return jsonify({"error": "userID is required"}), 400

    q = _register(user_id)

    def generate():
        try:
            yield f"event: connected\ndata: {json.dumps({'userID': user_id})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=HEARTBEAT_INTERVAL)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
                except queue.Empty:
                    # Heartbeat keeps the connection alive through proxies
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            _unregister(user_id, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx / Kong buffering
            "Connection": "keep-alive",
        },
    )


@app.route("/sse/notify", methods=["POST"])
def notify():
    """Internal endpoint: any service POSTs here to push an event to a user."""
    body = request.get_json(silent=True) or {}
    user_id = body.get("userID", "").strip()
    event = body.get("event", "notification")
    data = body.get("data", {})
    if not user_id:
        return jsonify({"error": "userID is required"}), 400
    pushed = _push(user_id, event, data)
    return jsonify({"pushed": True, "connections": pushed})


@app.route("/health")
def health():
    with _clients_lock:
        unique_users = len(_clients)
        total_connections = sum(len(v) for v in _clients.values())
    return jsonify({
        "status": "ok",
        "service": "sse-service",
        "connected_users": unique_users,
        "total_connections": total_connections,
    })


# ── RabbitMQ consumer (background thread) ─────────────────────────────────

def _rabbitmq_consumer():
    """
    Consumes from the `sse_exchange` topic exchange.
    Expected message format: {"userID": "...", "event": "...", "data": {...}}
    Routing key: notify.<userID>  (or just notify.# to catch all)
    """
    rabbitmq_url = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
            ch = conn.channel()
            ch.exchange_declare(exchange="sse_exchange", exchange_type="topic", durable=True)
            # Exclusive auto-delete queue — one per SSE service instance
            result = ch.queue_declare(queue="", exclusive=True)
            queue_name = result.method.queue
            ch.queue_bind(exchange="sse_exchange", queue=queue_name, routing_key="notify.#")

            def on_message(_ch, _method, _props, body):
                try:
                    msg = json.loads(body)
                    uid = msg.get("userID", "")
                    evt = msg.get("event", "notification")
                    dat = msg.get("data", {})
                    if uid:
                        _push(uid, evt, dat)
                except Exception:
                    pass

            ch.basic_consume(queue=queue_name, on_message_callback=on_message, auto_ack=True)
            ch.start_consuming()
        except Exception:
            time.sleep(5)


threading.Thread(target=_rabbitmq_consumer, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5060, debug=False, threaded=True)
