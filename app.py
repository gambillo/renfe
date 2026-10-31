
import os
import re
import math
import logging
import threading

from datetime import datetime, timezone
from time import time
from typing import Any
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template


app = Flask(__name__)

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

TIMEOUT = float(os.getenv("TIMEOUT", "10"))
REFRESH_HINT_SECONDS = 20

# Cuánto tiempo conservamos el itinerario de cada tren.
DETAIL_CACHE_SECONDS = 900

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("adif-rail-monitor")


# ============================================================
# COMUNIDADES AUTÓNOMAS
# ============================================================

CITY_PATTERNS = {
    "Andalucía": r"\bm[aá]laga\b|\bsevilla\b|\bc[oó]rdoba\b|\bgranada\b|\bc[aá]diz\b|\bjerez\b|\balmer[ií]a\b|\bhuelva\b|\bja[eé]n\b|\bantequera\b|\bpuente genil\b|\b[aá]lora\b",
    "Aragón": r"\bzaragoza\b|\bhuesca\b|\bteruel\b|\bcalatayud\b",
    "Principado de Asturias": r"\boviedo\b|\bgij[oó]n\b|\bavil[eé]s\b",
    "Illes Balears": r"\bpalma de mallorca\b|\binca\b|\bmanacor\b",
    "Canarias": r"\blas palmas\b|\btenerife\b|\bsanta cruz de tenerife\b",
    "Cantabria": r"\bsantander\b|\btorrelavega\b",
    "Castilla-La Mancha": r"\btoledo\b|\balbacete\b|\bciudad real\b|\bcuenca\b|\bguadalajara\b|\bpuertollano\b",
    "Castilla y León": r"\bvalladolid\b|\ble[oó]n\b|\bburgos\b|\bsalamanca\b|\bzamora\b|\b[aá]vila\b|\bsegovia\b|\bsoria\b|\bpalencia\b|\bmiranda de ebro\b",
    "Cataluña": r"\bbarcelona\b|\bgirona\b|\btarragona\b|\blleida\b|\bfigueres\b|\breus\b",
    "Comunitat Valenciana": r"\bval[eè]ncia\b|\balicante\b|\balacant\b|\bcastell[oó]n\b|\bcastell[oó]\b|\belche\b|\bx[aà]tiva\b|\bbenic[aà]ssim\b",
    "Extremadura": r"\bbadajoz\b|\bc[aá]ceres\b|\bm[eé]rida\b|\bplasencia\b",
    "Galicia": r"\ba coru[nñ]a\b|\bsantiago de compostela\b|\bvigo\b|\bourense\b|\blugo\b|\bpontevedra\b|\bferrol\b",
    "Comunidad de Madrid": r"\bmadrid\b|\bchamart[ií]n\b|\batocha\b|\bpr[ií]ncipe p[ií]o\b",
    "Región de Murcia": r"\bmurcia\b|\bcartagena\b|\blorca\b",
    "Comunidad Foral de Navarra": r"\bpamplona\b|\biru[nñ]a\b",
    "País Vasco": r"\bbilbao\b|\bdonostia\b|\bsan sebasti[aá]n\b|\bvitoria\b|\bgasteiz\b|\bir[uú]n\b",
    "La Rioja": r"\blogro[nñ]o\b",
}


# ============================================================
# ALIAS DE CAMPOS
# ============================================================

FIELD_ALIASES = {
    "id": ("id", "trainId", "vehicleId", "uuid"),
    "trainCode": ("trainCode", "code", "number", "trainNumber", "numTren"),
    "productName": ("productName", "trainType", "serviceName", "category", "product"),
    "operator": ("operator", "company", "operatorName", "companyName", "railway"),
    "originName": ("originName", "origin", "from", "departureStation", "originStation"),
    "destinationName": ("destinationName", "destination", "to", "arrivalStation", "destinationStation"),
    "nextStationName": ("nextStationName", "nextStation", "next_stop", "nextStop"),
    "delayMinutes": ("delayMinutes", "delay", "delay_min", "lateMinutes", "retraso"),
    "speedKmh": ("speedKmh", "speed", "velocity", "velocidad", "speed_kmh"),
    "expectedArrival": (
        "expectedArrival",
        "scheduledArrival",
        "plannedArrival",
        "arrivalScheduled",
        "horaLlegadaPrevista",
        "nextStationArrival"
    ),
    "estimatedArrival": (
        "estimatedArrival",
        "predictedArrival",
        "forecastArrival",
        "arrivalEstimate",
        "horaLlegadaEstimada"
    ),
    "latitude": ("latitude", "lat", "y"),
    "longitude": ("longitude", "lon", "lng", "long", "x"),
    "km": ("km", "kilometer", "kilometre", "pk", "pointKm"),
    "timestamp": ("timestamp", "updatedAt", "lastUpdate", "lastUpdated"),
}


# ============================================================
# CACHÉS
# ============================================================

detail_cache = {}
position_history = {}

cache_lock = threading.Lock()

executor = ThreadPoolExecutor(max_workers=6)


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def first_present(obj: dict, names: tuple[str, ...]) -> Any:
    if not isinstance(obj, dict):
        return None

    for name in names:
        value = obj.get(name)

        if value is not None and value != "":
            return value

    return None


def number_or_none(value: Any) -> float | None:

    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):

        match = re.search(
            r"-?\d+(?:[.,]\d+)?",
            value.strip()
        )

        if match:
            try:
                return float(
                    match.group(0).replace(",", ".")
                )
            except ValueError:
                return None

    return None


def parse_datetime(value: Any):

    if value is None:
        return None

    try:

        if isinstance(value, (int, float)):

            # Los timestamps de Radar de Trenes pueden venir en milisegundos.
            timestamp = float(value)

            if timestamp > 100000000000:
                timestamp /= 1000

            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc
            ).astimezone()

        if isinstance(value, str):

            text = value.strip()

            if not text:
                return None

            # Timestamp numérico en formato texto.
            if re.fullmatch(r"\d{10,13}", text):
                return parse_datetime(float(text))

            parsed = datetime.fromisoformat(
                text.replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.astimezone()

            return parsed.astimezone()

    except (ValueError, TypeError, OverflowError, OSError):
        return None

    return None


def datetime_output(value: Any):

    parsed = parse_datetime(value)

    if parsed:
        return parsed.isoformat()

    return value


# ============================================================
# COMUNIDADES AUTÓNOMAS
# ============================================================

def community_values(raw: dict, normalized: dict) -> list[str]:

    direct = first_present(raw, (
        "autonomousCommunities",
        "autonomousCommunity",
        "community",
        "region",
        "comunidadAutonoma",
        "ccaa"
    ))

    if isinstance(direct, (list, tuple, set)):

        found = [
            str(x).strip()
            for x in direct
            if x
        ]

        if found:
            return list(dict.fromkeys(found))

    elif direct:

        return [str(direct).strip()]

    text = " ".join(
        str(normalized.get(k) or "")
        for k in (
            "originName",
            "destinationName",
            "nextStationName"
        )
    ).casefold()

    found = []

    for community, pattern in CITY_PATTERNS.items():

        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        ):
            found.append(community)

    return found


# ============================================================
# VELOCIDAD ESTIMADA POR GPS
# ============================================================

def haversine_km(lat1, lon1, lat2, lon2):

    earth_radius = 6371.0

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return 2 * earth_radius * math.asin(
        math.sqrt(a)
    )


def estimate_speed(train):

    lat = train.get("latitude")
    lon = train.get("longitude")

    if lat is None or lon is None:
        return None

    if train.get("stale") is True:
        return None

    train_id = str(
        train.get("id") or train.get("trainCode") or ""
    )

    if not train_id:
        return None

    current_time = parse_datetime(
        train.get("timestamp")
    )

    if current_time is None:
        current_time = datetime.now().astimezone()

    current_timestamp = current_time.timestamp()

    with cache_lock:

        previous = position_history.get(train_id)

        position_history[train_id] = {
            "lat": lat,
            "lon": lon,
            "timestamp": current_timestamp
        }

    if not previous:
        return None

    elapsed = (
        current_timestamp - previous["timestamp"]
    )

    # Evitamos cálculos con muestras demasiado próximas
    # o con datos excesivamente antiguos.
    if elapsed < 15 or elapsed > 600:
        return None

    distance = haversine_km(
        previous["lat"],
        previous["lon"],
        lat,
        lon
    )

    speed = distance / (elapsed / 3600)

    # Evitamos mostrar velocidades físicamente inverosímiles.
    if speed < 1 or speed > 350:
        return None

    return round(speed, 1)


# ============================================================
# CONSULTA DEL ITINERARIO DE UN TREN
# ============================================================

def fetch_train_details(train_id):

    now = time()

    with cache_lock:

        cached = detail_cache.get(train_id)

        if cached and now - cached["time"] < DETAIL_CACHE_SECONDS:
            return cached["data"]

    url = (
        f"{TRAIN_API_BASE}/trains/"
        f"{quote(str(train_id), safe='')}/stops"
    )

    try:

        response = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ADIF-Rail-Monitor/2.0"
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        detail = data.get("detail", data)

        if not isinstance(detail, dict):
            return {}

        result = {
            "operatorCode": detail.get("operator"),
            "product": detail.get("product"),
            "trafficType": detail.get("trafficType"),
            "stops": []
        }

        stops = detail.get("stops", [])

        if isinstance(stops, list):

            for stop in stops:

                if not isinstance(stop, dict):
                    continue

                result["stops"].append({
                    "stationCode": stop.get("stationCode"),
                    "stationName": stop.get("stationName"),
                    "stopType": stop.get("stopType"),
                    "arrivalTime": datetime_output(
                        stop.get("arrivalTime")
                    ),
                    "arrivalDelay": number_or_none(
                        stop.get("arrivalDelay")
                    ),
                    "departureTime": datetime_output(
                        stop.get("departureTime")
                    ),
                    "departureDelay": number_or_none(
                        stop.get("departureDelay")
                    )
                })

        with cache_lock:

            detail_cache[train_id] = {
                "time": time(),
                "data": result
            }

        return result

    except requests.exceptions.RequestException as error:

        log.warning(
            "No se pudo consultar el itinerario %s: %s",
            train_id,
            error
        )

    except (ValueError, TypeError):

        log.warning(
            "Respuesta de itinerario no válida para %s",
            train_id
        )

    return {}


def enrich_train(train, detail):

    if not detail:
        return train

    # El proveedor facilita un código de operador.
    # No lo convertimos a una marca comercial sin una equivalencia verificada.
    operator_code = detail.get("operatorCode")

    if operator_code:

        train["operatorCode"] = operator_code

        if not train.get("operator"):
            train["operator"] = operator_code

    if detail.get("product"):
        train["providerProduct"] = detail["product"]

    if detail.get("trafficType"):
        train["trafficType"] = detail["trafficType"]

    stops = detail.get("stops", [])

    train["stops"] = stops

    # Buscamos la próxima estación en el itinerario.
    next_station = str(
        train.get("nextStationName") or ""
    ).strip().casefold()

    matching_stop = None

    if next_station:

        for stop in stops:

            station = str(
                stop.get("stationName") or ""
            ).strip().casefold()

            if station == next_station:
                matching_stop = stop
                break

    if matching_stop:

        scheduled = parse_datetime(
            matching_stop.get("arrivalTime")
        )

        delay = number_or_none(
            matching_stop.get("arrivalDelay")
        )

        if scheduled:

            train["expectedArrival"] = scheduled.isoformat()

            if delay is not None:

                estimated = (
                    scheduled.timestamp()
                    + delay * 60
                )

                train["estimatedArrival"] = (
                    datetime.fromtimestamp(
                        estimated,
                        tz=timezone.utc
                    ).astimezone().isoformat()
                )

    return train


# ============================================================
# NORMALIZACIÓN DE TRENES
# ============================================================

def normalize_train(item: Any) -> dict | None:

    if not isinstance(item, dict):
        return None

    result = dict(item)

    for target, aliases in FIELD_ALIASES.items():

        value = first_present(item, aliases)

        if value is not None:
            result[target] = value

    delay = number_or_none(
        result.get("delayMinutes")
    )

    speed = number_or_none(
        result.get("speedKmh")
    )

    lat = number_or_none(
        result.get("latitude")
    )

    lon = number_or_none(
        result.get("longitude")
    )

    result["delayMinutes"] = delay

    result["speedKmh"] = speed
    result["speed"] = speed

    result["speedSource"] = (
        "real"
        if speed is not None
        else None
    )

    if lat is not None and -90 <= lat <= 90:
        result["latitude"] = lat
    else:
        result["latitude"] = None

    if lon is not None and -180 <= lon <= 180:
        result["longitude"] = lon
    else:
        result["longitude"] = None

    communities = community_values(
        item,
        result
    )

    result["autonomousCommunities"] = communities

    result["autonomousCommunity"] = (
        communities[0]
        if len(communities) == 1
        else None
    )

    result["expectedArrival"] = datetime_output(
        result.get("expectedArrival")
    )

    result["estimatedArrival"] = datetime_output(
        result.get("estimatedArrival")
    )

    result["timestamp"] = datetime_output(
        result.get("timestamp")
    )

    return result


# ============================================================
# ENDPOINT PRINCIPAL
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/trains")
def get_trains():

    try:

        response = requests.get(
            ADIF_API,
            headers={
                "Accept": "application/json",
                "User-Agent": "ADIF-Rail-Monitor/2.0"
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):

            raw_trains = data

        elif isinstance(data, dict):

            raw_trains = data.get("trains")

            if raw_trains is None:
                raw_trains = data.get(
                    "fleet",
                    data.get("data", [])
                )

        else:

            raw_trains = []

        if isinstance(raw_trains, dict):
            raw_trains = list(raw_trains.values())

        if not isinstance(raw_trains, list):
            raw_trains = []

        trains = []

        for item in raw_trains:

            normalized = normalize_train(item)

            if normalized is not None:
                trains.append(normalized)

        # Consultamos los itinerarios en paralelo.
        # La caché evita repetir estas peticiones continuamente.
        futures = {}

        for train in trains:

            train_id = (
                train.get("id")
                or train.get("trainCode")
            )

            if train_id:

                futures[
                    executor.submit(
                        fetch_train_details,
                        str(train_id)
                    )
                ] = train

        for future in as_completed(futures):

            train = futures[future]

            try:

                detail = future.result()

                enrich_train(
                    train,
                    detail
                )

            except Exception:

                log.exception(
                    "Error enriqueciendo un tren"
                )

        # Calculamos velocidad solo cuando no existe
        # una velocidad real proporcionada por el proveedor.
        for train in trains:

            if train.get("speedKmh") is None:

                estimated_speed = estimate_speed(
                    train
                )

                train["speedKmh"] = estimated_speed
                train["speed"] = estimated_speed

                train["speedSource"] = (
                    "estimated"
                    if estimated_speed is not None
                    else None
                )

        # Información de calidad de los datos.
        with_speed = sum(
            1 for t in trains
            if t.get("speedKmh") is not None
        )

        with_operator = sum(
            1 for t in trains
            if t.get("operator")
        )

        with_stops = sum(
            1 for t in trains
            if t.get("stops")
        )

        return jsonify({

            "success": True,

            "total": len(trains),

            "updated": datetime.now().astimezone().isoformat(),

            "refreshSeconds": REFRESH_HINT_SECONDS,

            "coverage": {
                "withSpeed": with_speed,
                "withOperator": with_operator,
                "withStops": with_stops
            },

            "trains": trains
        })

    except requests.exceptions.Timeout:

        log.warning(
            "Timeout consultando la API ferroviaria"
        )

        return jsonify({
            "success": False,
            "error": "Tiempo de espera agotado al consultar la API",
            "trains": []
        }), 504

    except requests.exceptions.RequestException:

        log.exception(
            "Error de conexión con la API ferroviaria"
        )

        return jsonify({
            "success": False,
            "error": "No se ha podido consultar la fuente ferroviaria",
            "trains": []
        }), 502

    except (ValueError, TypeError):

        log.exception(
            "Respuesta JSON no válida o con formato inesperado"
        )

        return jsonify({
            "success": False,
            "error": "La API no ha devuelto datos JSON con el formato esperado",
            "trains": []
        }), 502


# ============================================================
# ESTADO DEL SERVIDOR
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status": "online",

        "application": "ADIF Rail Monitor",

        "port": PORT,

        "source": "Radar de Trenes",

        "detailCacheSeconds": DETAIL_CACHE_SECONDS

    })


# ============================================================
# ARRANQUE
# ============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("       ADIF RAIL MONITOR")
    print("=" * 50)

    print(f"Servidor local: http://127.0.0.1:{PORT}")
    print(f"Red local:      http://0.0.0.0:{PORT}")

    print("=" * 50)

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        threaded=True
    )
