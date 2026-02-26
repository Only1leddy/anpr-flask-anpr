# region imports

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
import hailo
import time
import datetime
import os
from hailo_apps.python.pipeline_apps.paddle_ocr.paddle_ocr_pipeline import GStreamerPaddleOCRApp
from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
import cv2
import re
hailo_logger = get_logger(__name__)
# endregion imports
PLATE_PATTERN = re.compile(r"[A-Z]{1,3}\d{1,4}[A-Z]{0,3}")  # simple letters + numbers
import requests
import json
from datetime import datetime
CONTROL_FILE = "/home/leddy/control.json"
last_saved = {}
SAVE_COOLDOWN = 5  # seconds
    
def image_saving_enabled():
    try:
        if os.path.exists(CONTROL_FILE):
            with open(CONTROL_FILE) as f:
                data = json.load(f)
                return data.get("save_images", False)
    except:
        pass
    return False

def is_valid_plate(text):
    """Return True if text looks like a registration plate."""
    t = text.replace(" ", "").upper()
    return bool(PLATE_PATTERN.fullmatch(t))
# -------------------------------
# User Callback Class with cooldown, movement, and text consistency
# -------------------------------
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.ocr_results = []
        self.plate_tracking = {}           # plate_id -> (cx, cy, last_trigger_time)
        self.cooldown_seconds = 2          # cooldown between saves
        self.text_consistency = {}         # text -> list of saved image paths
        self.consistency_threshold = 5     # how many repeated detections before ready

    def get_ocr_results(self):
        return self.ocr_results

    def add_ocr_result(self, text, confidence, bbox):
        self.ocr_results.append({'text': text, 'confidence': confidence, 'bbox': bbox})

    def clear_ocr_results(self):
        self.ocr_results.clear()

    # -------------------------------
    # Cooldown + movement logic
    # -------------------------------
    def can_trigger_plate(self, plate_id, cx, cy, movement_threshold=10):
        now = time.time()
        last_entry = self.plate_tracking.get(plate_id, (0, 0, 0))
        last_cx, last_cy, last_time = last_entry

        distance_moved = ((cx - last_cx) ** 2 + (cy - last_cy) ** 2) ** 0.5
        if (now - last_time >= self.cooldown_seconds) or (distance_moved > movement_threshold):
            self.plate_tracking[plate_id] = (cx, cy, now)
            return True
        return False

    # -------------------------------
    # Update text consistency buffer
    # -------------------------------
    def update_text_consistency(self, text, save_path):
        if text not in self.text_consistency:
            self.text_consistency[text] = []
        self.text_consistency[text].append(save_path)

        if len(self.text_consistency[text]) >= self.consistency_threshold:
            send_plate_to_server(text)
            print(f"✅ Plate '{text}' confirmed {self.consistency_threshold} times. Ready for API, (SENT TO FLASK!)") 
            # optionally clear after API-ready
            self.text_consistency[text] = []

# -------------------------------
# Helper to save plate crops and CSV
# -------------------------------
def save_plate_crop_and_text(frame, bbox, text, confidence, folder="/home/leddy/anpr_captures", user_data=None):
    os.makedirs(folder, exist_ok=True)
    #x1 = int(bbox.xmin() * frame.shape[1])
    #y1 = int(bbox.ymin() * frame.shape[0])
    #x2 = int(bbox.xmax() * frame.shape[1])
    #y2 = int(bbox.ymax() * frame.shape[0])
    #crop = frame[y1:y2, x1:x2]

    #if crop.size > 0:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = f"{folder}/plate_{timestamp}.jpg"
    # Save image
    #import cv2
    #cv2.imwrite(save_path, crop)
    #print(f"💾 Saved plate: {save_path}", flush=True)
    # Save CSV line
    csv_path = f"{folder}/plates.csv"
    with open(csv_path, "a") as f:
        f.write(f"{timestamp},{text},{confidence},{save_path}\n")
    # Update consistency tracking
    if user_data:
        user_data.update_text_consistency(text, save_path)
    
    
    
    if image_saving_enabled():
        now = datetime.now()
        if text not in last_saved or (now - last_saved[text]).seconds > SAVE_COOLDOWN:
            last_saved[text] = now           
            x1 = int(bbox.xmin() * frame.shape[1])
            y1 = int(bbox.ymin() * frame.shape[0])
            x2 = int(bbox.xmax() * frame.shape[1])
            y2 = int(bbox.ymax() * frame.shape[0])
            crop = frame[y1:y2, x1:x2]

            if crop.size > 0:

                cv2.imwrite(save_path, crop)
                
                # NEW: Always overwrite this file
                last_path = "/home/leddy/anpr_captures/lastplate.jpg"
                cv2.imwrite(last_path, crop)

                print(f"💾 Saved plate: {save_path}", flush=True)
        
#-------------------------------------------------#
###########--- send plate to flask ---#############
#-------------------------------------------------#
def send_plate_to_server(plate_text):
    try:
        requests.post(
            "http://127.0.0.1:5000/detect",
            json={"plate": plate_text},
            timeout=5
        )
    except Exception as e:
        print(f"Failed to send plate: {e}")

# -------------------------------
# User Callback Function
# -------------------------------
def app_callback(element, buffer, user_data):
    if buffer is None:
        hailo_logger.warning("Received None buffer.")
        return

    user_data.increment()
    user_data.clear_ocr_results()

    pad = element.get_static_pad("src")
    format, width, height = get_caps_from_pad(pad)

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    for det in detections:
        label = det.get_label()
        bbox = det.get_bbox()
        confidence = det.get_confidence()

        # Plate filtering logic: only text regions & reasonable confidence
        if label != "text_region" or confidence < 0.12:
            continue

        # Get OCR result text
        text_result = ""
        ocr_objects = det.get_objects_typed(hailo.HAILO_CLASSIFICATION)
        for cls in ocr_objects:
            if cls.get_classification_type() == "text_region":
                text_result = cls.get_label()
                break
        if not text_result and ocr_objects:
            text_result = ocr_objects[0].get_label()
        if not text_result.strip():
            continue

        user_data.add_ocr_result(text_result, confidence, bbox)

        # Compute plate center & ID for tracking
        x1 = int(bbox.xmin() * width)
        y1 = int(bbox.ymin() * height)
        x2 = int(bbox.xmax() * width)
        y2 = int(bbox.ymax() * height)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        plate_id = f"{round(cx, -1)}_{round(cy, -1)}"

        # Only extract & save frame if cooldown/movement allows
        if user_data.can_trigger_plate(plate_id, cx, cy):
            frame = get_numpy_from_buffer(buffer, format, width, height)
            if frame is not None:
                if is_valid_plate(text_result):
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    save_plate_crop_and_text(frame_bgr, bbox, text_result, confidence, user_data=user_data)

    return

# -------------------------------
# Main
# -------------------------------
def main():
    hailo_logger.info("Starting OCR App with cooldown, tracking, and consistency check...")
    user_data = user_app_callback_class()
    app = GStreamerPaddleOCRApp(app_callback, user_data)
    app.run()

if __name__ == "__main__":
    main()
