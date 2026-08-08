from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timezone
from pathlib import Path
from werkzeug.utils import secure_filename
from collections import deque
import json
import math
import uuid

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "environment_reports.json"
UPLOAD_DIR = BASE_DIR / "report_attachments"
UPLOAD_DIR.mkdir(exist_ok=True)

STATUSES = ["New", "In Review", "Action Taken", "Resolved"]
CATEGORIES = ["temperature", "humidity", "pressure", "air-quality"]
SEVERITIES = ["normal", "watch", "alert"]
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "txt", "csv", "json", "doc", "docx"}

latest_data = {}
latest_prediction = {}
reports = []
recent_readings = deque(maxlen=20)
active_events = set()

# Sudden-change thresholds are intentionally conservative for a 3-second sensor loop.
SUDDEN_CHANGE = {
    "temperature": 3.0,      # °C between readings
    "humidity": 15.0,         # percentage points
    "pressure": 8.0,          # hPa
    "air-quality": 0.40,      # 40% relative gas-resistance change
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def json_safe(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def load_reports():
    global reports
    try:
        reports = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if not isinstance(reports, list):
            reports = []
    except (FileNotFoundError, json.JSONDecodeError):
        reports = []


def save_reports():
    DATA_FILE.write_text(json.dumps(reports, indent=2), encoding="utf-8")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_category(category):
    aliases = {
        "Temperature": "temperature",
        "Humidity": "humidity",
        "Pressure": "pressure",
        "Air Quality": "air-quality",
        "air quality": "air-quality",
    }
    return aliases.get(category, category)


def classify_reading(category, value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if category == "temperature":
        if value < 10 or value > 35:
            return "alert"
        if value < 15 or value > 30:
            return "watch"
    elif category == "humidity":
        if value < 20 or value > 80:
            return "alert"
        if value < 30 or value > 70:
            return "watch"
    elif category == "pressure":
        if value < 970 or value > 1040:
            return "alert"
        if value < 980 or value > 1030:
            return "watch"
    elif category == "air-quality":
        # Lower BME680 gas resistance is treated as a poorer air-quality signal.
        if value < 5000:
            return "alert"
        if value < 10000:
            return "watch"
    return None


def current_value(data, category):
    return data.get("gas_resistance") if category == "air-quality" else data.get(category)


def detect_sudden_change(category, current, previous):
    if current is None or previous is None:
        return False, None
    try:
        current = float(current)
        previous = float(previous)
    except (TypeError, ValueError):
        return False, None

    if category == "air-quality":
        if previous == 0:
            return False, None
        change = abs(current - previous) / abs(previous)
        if change >= SUDDEN_CHANGE[category]:
            direction = "decreased" if current < previous else "increased"
            return True, f"Gas resistance {direction} by {change * 100:.0f}%"
    else:
        change = abs(current - previous)
        if change >= SUDDEN_CHANGE[category]:
            direction = "decreased" if current < previous else "increased"
            return True, f"{category.replace('-', ' ').title()} {direction} by {change:.1f}"
    return False, None


def make_report(category, severity, snapshot, location="NODE-01", source="automatic", notes="", attachment=None):
    timestamp = now_iso()
    report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
    report = {
        "id": report_id,
        "timestamp": timestamp,
        "created_at": timestamp,
        "category": category,
        "severity": severity,
        "readings": {k: json_safe(v) for k, v in (snapshot or {}).items()},
        "status": "New",
        "location": location,
        "source": source,
        "auto_generated": source == "automatic",
        "notes": notes,
        "timeline": [{"status": "New", "timestamp": timestamp}],
        "status_history": [{"status": "New", "at": timestamp}],
        "attachment": attachment,
    }
    reports.insert(0, report)
    del reports[250:]
    save_reports()
    return report


def process_anomalies(data):
    location = str(data.get("device_id") or data.get("location") or "NODE-01")
    previous = recent_readings[-1] if recent_readings else None
    created = []

    for category in CATEGORIES:
        value = current_value(data, category)
        threshold_severity = classify_reading(category, value)
        sudden, reason = detect_sudden_change(category, value, current_value(previous, category) if previous else None)
        event_type = "threshold" if threshold_severity else ("sudden-change" if sudden else None)
        key = f"{location}:{category}:{event_type or 'none'}"

        if event_type:
            if event_type == "threshold":
                severity = threshold_severity
                note = f"Automatic threshold anomaly detected in {category.replace('-', ' ')}."
            else:
                severity = "alert" if category in {"temperature", "air-quality"} else "watch"
                note = f"Automatic sudden-change detection: {reason}."

            if key not in active_events:
                created.append(make_report(category, severity, data, location, "automatic", note))
                active_events.add(key)
        else:
            active_events.discard(f"{location}:{category}:threshold")
            active_events.discard(f"{location}:{category}:sudden-change")

    recent_readings.append(dict(data))
    return created


load_reports()


@app.route("/data", methods=["POST"])
def receive_data():
    global latest_data
    latest_data = request.get_json(silent=True) or {}
    created = process_anomalies(latest_data)
    print(f"Got data: {latest_data} | auto reports created: {len(created)}")
    return jsonify({"status": "ok", "reports_created": created})


@app.route("/predictions", methods=["POST"])
def receive_prediction():
    global latest_prediction
    latest_prediction = request.get_json(silent=True) or {}
    return jsonify({"status": "ok"})


@app.route("/api/data", methods=["GET"])
def get_data():
    return jsonify({"live": latest_data, "predicted": latest_prediction})


@app.route("/api/reports", methods=["GET"])
def get_reports():
    items = list(reports)
    category = normalize_category(request.args.get("category", ""))
    severity = request.args.get("severity", "").lower()
    status = request.args.get("status", "")
    location = request.args.get("location", "")
    date = request.args.get("date", "")

    if category:
        items = [r for r in items if r.get("category") == category]
    if severity:
        items = [r for r in items if r.get("severity", "").lower() == severity]
    if status:
        items = [r for r in items if r.get("status") == status]
    if location:
        items = [r for r in items if r.get("location") == location]
    if date:
        items = [r for r in items if str(r.get("timestamp", ""))[:10] == date]

    return jsonify(items)


@app.route("/api/reports", methods=["POST"])
def create_manual_report():
    # Supports both JSON requests and multipart/form-data with a real attachment.
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        payload = request.form.to_dict()
        file = request.files.get("attachment")
    else:
        payload = request.get_json(silent=True) or {}
        file = None

    category = normalize_category(payload.get("category", "air-quality"))
    if category not in CATEGORIES:
        return jsonify({"error": "Invalid category"}), 400

    severity = (payload.get("severity") or "").lower()
    if severity not in SEVERITIES:
        severity = classify_reading(category, current_value(latest_data, category)) or "normal"

    location = str(payload.get("location") or latest_data.get("device_id") or "NODE-01")
    notes = str(payload.get("notes") or "Manually logged from dashboard.")
    attachment = None

    if file and file.filename:
        if not allowed_file(file.filename):
            return jsonify({"error": "Unsupported attachment type"}), 400
        original = secure_filename(file.filename)
        stored_name = f"{uuid.uuid4().hex[:12]}_{original}"
        file.save(UPLOAD_DIR / stored_name)
        attachment = {
            "original_name": original,
            "stored_name": stored_name,
            "url": f"/api/reports/attachments/{stored_name}",
        }

    report = make_report(category, severity, latest_data, location, "manual", notes, attachment)
    return jsonify(report), 201


@app.route("/api/reports/<report_id>", methods=["PATCH"])
def update_report(report_id):
    payload = request.get_json(silent=True) or {}
    report = next((r for r in reports if r["id"] == report_id), None)
    if report is None:
        return jsonify({"error": "Report not found"}), 404

    new_status = payload.get("status")
    if new_status and new_status not in STATUSES:
        return jsonify({"error": "Invalid status"}), 400

    if new_status and new_status != report.get("status"):
        timestamp = now_iso()
        report["status"] = new_status
        report.setdefault("timeline", []).append({"status": new_status, "timestamp": timestamp})
        report.setdefault("status_history", []).append({"status": new_status, "at": timestamp})
        save_reports()

    return jsonify(report)


@app.route("/api/reports/attachments/<path:filename>", methods=["GET"])
def get_attachment(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


@app.route("/api/reports/summary", methods=["GET"])
def report_summary():
    by_category = {category: sum(1 for r in reports if r.get("category") == category) for category in CATEGORIES}
    by_status = {status: sum(1 for r in reports if r.get("status") == status) for status in STATUSES}
    by_severity = {severity: sum(1 for r in reports if r.get("severity") == severity) for severity in SEVERITIES}
    hotspot = max(by_category.items(), key=lambda item: item[1]) if reports else ("none", 0)
    return jsonify({
        "total": len(reports),
        "total_reports": len(reports),
        "by_category": by_category,
        "by_status": by_status,
        "by_severity": by_severity,
        "top_advisory": {"category": hotspot[0], "count": hotspot[1]},
        "top_category": hotspot[0],
    })


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Attachment is too large. Maximum size is 10 MB."}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
