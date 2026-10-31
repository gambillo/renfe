import os
import re
import math
import logging
import threading
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from time import time
from typing import Any
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template, send_from_directory

app = Flask(__name__)
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
STATIC_DIR = Path(__file__).resolve().parent / "static"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

ADIF_API = os.getenv(
    "TRAIN_API",
    "https://radardetrenes.com/api/v1/fleet?type=all"
)
TRAIN_API_BASE = os.getenv(
    "TRAIN_API_BASE",
    "https://radardetrenes.com/api/v1"
)

GTFS_LD_POSITIONS = os.getenv(
    "GTFS_LD_POSITIONS",
    "https://gtfsrt.renfe.com/vehicle_positions_LD.json"
)
GTFS_LD_UPDATES = os.getenv(
    "GTFS_LD_UPDATES",
    "https://gtfsrt.renfe.com/trip_updates_LD.json"
)
GTFS_CERCANIAS_POSITIONS = os.getenv(
    "GTFS_CERCANIAS_POSITIONS",
    "https://gtfsrt.renfe.com/vehicle_positions.json"
)

TIMEOUT = float(os.getenv("TIMEOUT", "12"))
REFRESH_HINT_SECONDS = 30
DETAIL_CACHE_SECONDS = 900
MADRID = ZoneInfo("Europe/Madrid")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("adif-rail-monitor")

session = requests.Session()
session.headers.update({
    "Accept": "application/json",
    "User-Agent": "ADIF-Rail-Monitor/3.0"
})
