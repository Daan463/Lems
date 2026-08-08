from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid

app = Flask(__name__)
CORS(app)

DATA_FILE = Path(__file__).with_name("environment_reports.json")
STATUSES = ["New", "In Review", "Action Taken", "Resolved"]
CATEGORIES = ["temperature", "humidity", "pressure", "air-quality"]

latest_data = {}
latest_prediction = {}
reports = []
active_anomalies = set()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_reports():
    global reports
    try:
        reports = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        reports = []


def save_reports():
    DATA_FILE.write_text(json.dumps(reports, indent=2), encoding="utf-8")


def make_report(category, severity, snapshot, location="NODE-01", source="automatic"):
    timestamp = now_iso()
    report = {
        "id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
        "timestamp": timestamp,
        "category": category,
        "severity": severity,
        "readings": dict(snapshot),
        "status": "New",
        "location": location,
        "source": source,
        "timeline": [{"status": "New", "timestamp": timestamp}],
    }
    reports.insert(0, report)
    # Keep the lightweight demo store bounded while retaining recent history.
    del reports[250:]
    save_reports()
    return report


def classify_reading(category, value):
    if value is None:
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
        # BME680 gas resistance: lower resistance generally indicates more VOCs.
        if value < 5000:
            return "alert"
        if value < 10000:
            return "watch"
    return None


def process_anomalies(data):
    location = str(data.get("device_id") or data.get("location") or "NODE-01")
    mapping = {
        "temperature": data.get("temperature"),
        "humidity": data.get("humidity"),
        "pressure": data.get("pressure"),
        "air-quality": data.get("gas_resistance"),
    }
    created = []
    for category, value in mapping.items():
        severity = classify_reading(category, value)
        key = f"{location}:{category}"
        if severity:
            # A report is created when an anomaly begins, not every 3-second sensor tick.
            if key not in active_anomalies:
                created.append(make_report(category, severity, data, location, "automatic"))
                active_anomalies.add(key)
        else:
            active_anomalies.discard(key)
    return created


load_reports()


@app.route("/data", methods=["POST"])
def receive_data():
    global latest_data
    latest_data = request.get_json(silent=True) or {}
    print("Got data:", latest_data)
    process_anomalies(latest_data)
    return jsonify({"status": "ok"})


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
    return jsonify({"reports": reports})


@app.route("/api/reports", methods=["POST"])
def create_manual_report():
    payload = request.get_json(silent=True) or {}
    category = payload.get("category", "air-quality")
    if category not in CATEGORIES:
        return jsonify({"error": "Invalid category"}), 400
    severity = payload.get("severity") or classify_reading(category, latest_data.get("gas_resistance" if category == "air-quality" else category)) or "watch"
    location = str(payload.get("location") or latest_data.get("device_id") or "NODE-01")
    report = make_report(category, severity, latest_data, location, "manual")
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

    if new_status and new_status != report["status"]:
        report["status"] = new_status
        report["timeline"].append({"status": new_status, "timestamp": now_iso()})
        save_reports()

    return jsonify(report)


@app.route("/api/reports/summary", methods=["GET"])
def report_summary():
    by_category = {category: sum(1 for r in reports if r["category"] == category) for category in CATEGORIES}
    by_status = {status: sum(1 for r in reports if r["status"] == status) for status in STATUSES}
    by_severity = {severity: sum(1 for r in reports if r["severity"] == severity) for severity in ["normal", "watch", "alert"]}
    hotspot = max(by_category.items(), key=lambda item: item[1]) if reports else ("none", 0)
    return jsonify({
        "total": len(reports),
        "by_category": by_category,
        "by_status": by_status,
        "by_severity": by_severity,
        "top_advisory": {"category": hotspot[0], "count": hotspot[1]},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
