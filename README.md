# anpr-flask-anpr

# 🚗 AMPR – Flask ANPR System

Automatic Number Plate Recognition system using Flask + PaddleOCR.

This repo contains the WEB + OCR layer only.

It does NOT include hardware acceleration or detection pipeline.

---

## 🔧 Prerequisites

You MUST have:

- Python 3.9+
- HailoRT 4.23 installed
- Hailo device configured
- GStreamer installed
- Detection pipeline working

This project expects plate data to be sent to:

POST /detect

---

## 📦 Install Python Requirements

pip install -r requirements.txt

---

## ▶ Run

python app.py

Open browser:

http://localhost:5000

---

## 📁 Files

app.py – Flask backend  
index.html – Web interface  
ocr_pipeline.py – PaddleOCR integration  

---

## ❗ Not Included

- Hailo runtime files  
- GStreamer pipeline  
- Model weights  
- API keys  
