from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)   # <-- this line is essential, must come right after creating app

latest_data = {}
latest_prediction = {}

@app.route('/data', methods=['POST'])
def receive_data():
    global latest_data
    latest_data = request.get_json()
    print("Got data:", latest_data)
    return jsonify({"status": "ok"})

@app.route('/predictions', methods=['POST'])
def receive_prediction():
    global latest_prediction
    latest_prediction = request.get_json()
    return jsonify({"status": "ok"})

@app.route('/api/data', methods=['GET'])
def get_data():
    return jsonify({
        "live": latest_data,
        "predicted": latest_prediction
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)