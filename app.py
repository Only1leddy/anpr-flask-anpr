from flask import Flask, render_template, request, jsonify, send_file
import subprocess
import datetime
from datetime import datetime, timedelta
import requests
import os
import json
#----------------------#
#-------load stuff-----#
#----------------------#
from dotenv import load_dotenv
load_dotenv()

os.environ.get("NEW_API_KEY_MOT")
os.environ.get("NEW_API_KEY_TAX")

os.environ.get("DVSA_CLIENT_ID")
os.environ.get("DVSA_CLIENT_SECRET")
os.environ.get("DVSA_TOKEN_URL")
os.environ.get("DVSA_SCOPE")

base_url = os.environ.get("DVSA_MOT_BASE_URL")

#----------------------#

app = Flask(__name__)

dvsa_token = None
dvsa_token_expiry = None

COOLDOWN = timedelta(minutes=30)
anpr_process = None
detected_plates = []
mot_enabled = False
last_mot_check = {}   # { plate: datetime }
mot_results = {}      # { plate: result }
import os


CONTROL_FILE = "/home/leddy/control.json"

# Create default if missing
if not os.path.exists(CONTROL_FILE):
    with open(CONTROL_FILE, "w") as f:
        json.dump({"save_images": False}, f)

###########################################################################################
def get_dvsa_token():
    global dvsa_token, dvsa_token_expiry

    # If token exists and not expired, reuse it
    if dvsa_token and dvsa_token_expiry and datetime.now() < dvsa_token_expiry:
        return dvsa_token

    try:
        response = requests.post(
            os.environ.get("DVSA_TOKEN_URL"),
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ.get("DVSA_CLIENT_ID"),
                "client_secret": os.environ.get("DVSA_CLIENT_SECRET"),
                "scope": os.environ.get("DVSA_SCOPE")
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        if response.status_code == 200:
            token_data = response.json()

            dvsa_token = token_data["access_token"]

            # expires_in usually returned in seconds
            expires_in = token_data.get("expires_in", 3600)
            dvsa_token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

            return dvsa_token

        print("Token request failed:", response.text)
        return None

    except Exception as e:
        print("Token error:", e)
        return None
    
##########################################################################################    
#-------------------------#
#--- check plte mot PI ---#
#-------------------------#
def check_mot(plate):
    token = get_dvsa_token()
    if not token:
        return "Auth Failed"

    base_url = os.environ.get("DVSA_MOT_BASE_URL")
    endpoint = f"{base_url}/{plate}"

    try:
        response = requests.get(
            endpoint,
            headers = {
            "Authorization": f"Bearer {token}",
            "X-API-Key": os.environ.get("NEW_API_KEY_MOT"),
            "Accept": "application/json+v6"
            }
        )
        
        if response.status_code == 404:
            return "That didn’t work – Reg Not Found"
        
        if response.status_code == 200:
            data = response.json()
            
            # Check for motTests
            mot_tests = data.get("motTests", [])
            if not mot_tests:
                print('That didn’t work')
                mot_expiry = "REG ERROR"

            # Find the latest expiryDate
            expiry_date_str = None
            for test in mot_tests:
                if test.get("expiryDate"):
                    mot_expiry = test["expiryDate"]
                    break
            if not mot_expiry:
                print('That didn’t work')
                mot_expiry = "Duno"
            
            return f"Valid until {mot_expiry}"
        print(f"MOT Error {response.status_code}")
        return f"MOT Error {response.status_code}"

    except Exception as e:
        print("MOT request error:", e)
        return "MOT Failed"

# ------------------------#
# ----START ANPR----------#
# ------------------------#
@app.route("/startANPR", methods=["POST"])
def start_anpr():
    global anpr_process

    if anpr_process is None:
        anpr_process = subprocess.Popen(
            [
                "/home/leddy/hailo-apps-infra/venv_hailo_apps/bin/python3",
                "paddle_ocr6.py",
                "--input",
                "rpi"
            ],
            cwd="/home/leddy/hailo-apps-infra/hailo_apps/python/pipeline_apps/paddle_ocr"
        )
        return jsonify({"status": "started"})

    return jsonify({"status": "already running"})

# ------------------------
# STOP ANPR
# ------------------------
@app.route("/stopANPR", methods=["POST"])
def stop_anpr():
    global anpr_process

    if anpr_process:
        anpr_process.terminate()
        anpr_process = None
        return jsonify({"status": "stopped"})

    return jsonify({"status": "not running"})

#--- start mot api ---#
@app.route("/startMOT", methods=["POST"])
def start_mot():
    global mot_enabled
    mot_enabled = True
    return jsonify({"mot": "enabled"})

#--- stop mot api ---#
@app.route("/stopMOT", methods=["POST"])
def stop_mot():
    global mot_enabled
    mot_enabled = False
    return jsonify({"mot": "disabled"})

@app.route("/mot_status")
def mot_status():
    return jsonify({"mot_enabled": mot_enabled})

@app.route("/last_image")
def last_image():
    path = "/home/leddy/anpr_captures/lastplate.jpg"
    if os.path.exists(path):
        return send_file(path, mimetype="image/jpeg")
    return "", 404

# ------------------------
# RECEIVE PLATE FROM ANPR
# ------------------------
@app.route("/detect", methods=["POST"])
def detect():
    global mot_enabled

    data = request.get_json()
    plate = data.get("plate")
    now = datetime.now()

    mot_status = "Not Checked"

    # --- MOT LOGIC ---
    if mot_enabled:
        last_checked = last_mot_check.get(plate)

        if not last_checked or now - last_checked > COOLDOWN:
            mot_status = check_mot(plate)
            last_mot_check[plate] = now
        else:
            mot_status = "Cooldown Active"

    # --- Store detection ---
    detected_plates.insert(0, {
        "plate": plate,
        "time": now.strftime("%H:%M:%S"),
        "mot": mot_status
    })

    detected_plates[:] = detected_plates[:20]

    return jsonify({"status": "received"})

# ------------------------
# GET DETECTED PLATES
# ------------------------
@app.route("/plates")
def get_plates():
    return jsonify(detected_plates)


@app.route("/plates2")
def get_plates2():
    filtered = [
        p for p in detected_plates
        if p["mot"] != "Cooldown Active"
    ]
    return jsonify(filtered)


#########################################
@app.route("/toggle_images", methods=["POST"])
def toggle_images():
    state = request.json.get("enabled", False)

    with open("control.json", "w") as f:
        json.dump({"save_images": state}, f)

    return jsonify({"save_images": state})

# ------------------------
# INDEX PAGE
# ------------------------
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)