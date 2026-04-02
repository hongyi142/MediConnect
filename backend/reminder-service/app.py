"""
Appointment reminder service.

Runs an APScheduler job every minute that scans upcoming confirmed appointments
and fires reminders at two checkpoints:
  - 24 h before the appointment
  -  1 h before the appointment

Each reminder is sent as:
  1. An SSE push (real-time, if the patient is currently online)
  2. An email via the notification-wrapper (always attempted)

Redis is used to deduplicate: a reminder is only sent once per
(appointmentID, checkpoint) pair.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import redis
import requests
from apscheduler.schedulers.background import BackgroundScheduler

# ── Service URLs ───────────────────────────────────────────────────────────

APPOINTMENT_URL = os.environ.get(
    "APPOINTMENT_SERVICE_URL", "http://appointment-service:5032"
).rstrip("/")
SSE_URL = os.environ.get("SSE_SERVICE_URL", "http://sse-service:5060").rstrip("/")
PATIENT_URL = os.environ.get(
    "PATIENT_SERVICE_URL", "http://patient-service:5030"
).rstrip("/")
NOTIFICATION_URL = os.environ.get(
    "NOTIFICATION_WRAPPER_URL", "http://notification-wrapper:5011"
).rstrip("/")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# ── Redis client ───────────────────────────────────────────────────────────

_redis = redis.from_url(REDIS_URL, decode_responses=True)

# ── Reminder windows (±5 min tolerance) ──────────────────────────────────

REMINDERS = [
    {"key": "24h", "label": "24 hours", "hours": 24},
    {"key": "1h",  "label": "1 hour",   "hours": 1},
]
WINDOW_MINUTES = 5  # fire if slot_start is within [target - 5min, target + 5min]


# ── Helpers ────────────────────────────────────────────────────────────────

def _already_sent(appointment_id: str, reminder_key: str) -> bool:
    return bool(_redis.get(f"reminder:{appointment_id}:{reminder_key}"))


def _mark_sent(appointment_id: str, reminder_key: str) -> None:
    # TTL = 48 h so the key auto-expires after the appointment has passed
    _redis.setex(f"reminder:{appointment_id}:{reminder_key}", 172800, "1")


def _get_patient_email(patient_id: str) -> str | None:
    try:
        resp = requests.get(f"{PATIENT_URL}/patient/{patient_id}", timeout=5)
        if resp.ok:
            data = resp.json()
            return data.get("email") or data.get("data", {}).get("email")
    except Exception:
        pass
    return None


def _send_sse(patient_id: str, appointment_id: str, slot_start: str, label: str) -> None:
    try:
        requests.post(
            f"{SSE_URL}/sse/notify",
            json={
                "userID": patient_id,
                "event": "reminder",
                "data": {
                    "message": f"Reminder: your appointment is in {label} "
                               f"(at {slot_start[:16].replace('T', ' ')} UTC).",
                    "appointmentID": appointment_id,
                    "slotStart": slot_start,
                },
            },
            timeout=5,
        )
    except Exception:
        pass


def _send_email(patient_id: str, appointment_id: str, slot_start: str, label: str) -> None:
    email = _get_patient_email(patient_id)
    if not email:
        return
    slot_display = slot_start[:16].replace("T", " ") + " UTC"
    try:
        requests.post(
            f"{NOTIFICATION_URL}/notify/send",
            json={
                "receiver": email,
                "subject": f"MediConnect – Appointment reminder ({label})",
                "content": (
                    f"This is a reminder that you have an upcoming appointment "
                    f"in {label} (scheduled for {slot_display}).\n\n"
                    f"Appointment ID: {appointment_id}\n\n"
                    f"Please log in to MediConnect to view the details."
                ),
                "channel": "email",
            },
            timeout=10,
        )
    except Exception:
        pass


def _fire_reminder(appt: dict, reminder: dict) -> None:
    appt_id = appt.get("appointmentID", "")
    patient_id = appt.get("patientID", "")
    slot_start = appt.get("slotStart") or appt.get("dateTime", "")

    if not appt_id or not patient_id or not slot_start:
        return
    if _already_sent(appt_id, reminder["key"]):
        return

    label = reminder["label"]
    print(f"[reminder] Sending {reminder['key']} reminder for appointment {appt_id}", flush=True)

    _send_sse(patient_id, appt_id, slot_start, label)
    _send_email(patient_id, appt_id, slot_start, label)
    _mark_sent(appt_id, reminder["key"])


# ── Scheduler job ──────────────────────────────────────────────────────────

def check_reminders() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Build the broadest time window we need to query (now+55min → now+24h+5min)
    query_from = now + timedelta(minutes=55)
    query_to = now + timedelta(hours=24, minutes=WINDOW_MINUTES)

    try:
        resp = requests.get(
            f"{APPOINTMENT_URL}/appointment/upcoming",
            params={
                "status": "confirmed",
                "from": query_from.isoformat(),
                "to": query_to.isoformat(),
            },
            timeout=10,
        )
        if not resp.ok:
            return
        appointments = resp.json().get("appointments", [])
    except Exception as exc:
        print(f"[reminder] Failed to fetch appointments: {exc}", flush=True)
        return

    for appt in appointments:
        raw = appt.get("slotStart") or appt.get("dateTime")
        if not raw:
            continue
        try:
            slot_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if slot_dt.tzinfo:
                slot_dt = slot_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue

        for reminder in REMINDERS:
            target = slot_dt - timedelta(hours=reminder["hours"])
            if abs((now - target).total_seconds()) <= WINDOW_MINUTES * 60:
                _fire_reminder(appt, reminder)


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[reminder] Starting appointment reminder service…", flush=True)
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(check_reminders, "interval", minutes=1, id="check_reminders")
    scheduler.start()
    print("[reminder] Scheduler running — checking every minute.", flush=True)
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("[reminder] Scheduler stopped.", flush=True)
