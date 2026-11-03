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

# ============================================================
# COMUNIDADES AUTÓNOMAS (estimación a partir de estaciones)
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
    "Comunitat Valenciana": r"\bval[eè]ncia\b|\balicante\b|\balacant\b|\bcastell[oó]n\b|\belche\b|\bx[aà]tiva\b|\bbenic[aà]ssim\b",
    "Extremadura": r"\bbadajoz\b|\bc[aá]ceres\b|\bm[eé]rida\b|\bplasencia\b",
    "Galicia": r"\ba coru[nñ]a\b|\bsantiago de compostela\b|\bvigo\b|\bourense\b|\blugo\b|\bpontevedra\b|\bferrol\b",
    "Comunidad de Madrid": r"\bmadrid\b|\bchamart[ií]n\b|\batocha\b|\bpr[ií]ncipe p[ií]o\b",
    "Región de Murcia": r"\bmurcia\b|\bcartagena\b|\blorca\b",
    "Comunidad Foral de Navarra": r"\bpamplona\b|\biru[nñ]a\b",
    "País Vasco": r"\bbilbao\b|\bdonostia\b|\bsan sebasti[aá]n\b|\bvitoria\b|\bgasteiz\b|\bir[uú]n\b",
    "La Rioja": r"\blogro[nñ]o\b",
}

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
    "expectedArrival": ("expectedArrival", "scheduledArrival", "plannedArrival",
                        "arrivalScheduled", "horaLlegadaPrevista"),
    "estimatedArrival": ("estimatedArrival", "predictedArrival", "forecastArrival",
                         "arrivalEstimate", "horaLlegadaEstimada"),
    "latitude": ("latitude", "lat", "y"),
    "longitude": ("longitude", "lon", "lng", "long", "x"),
    "km": ("km", "kilometer", "kilometre", "pk", "pointKm"),
    "timestamp": ("timestamp", "updatedAt", "lastUpdate", "lastUpdated"),
    "nextStationExpectedArrival": ("nextStationArrival",),
}

detail_cache = {}
position_history = {}
cache_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=8)


# ============================================================
# UTILIDADES
# ============================================================

def first_present(obj: dict, names: tuple[str, ...]) -> Any:
    if not isinstance(obj, dict):
        return None
    for name in names:
        value = obj.get(name)
        if value is not None and value != "":
            return value
    return None


def number_or_none(value: Any):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:[.,]\d+)?", value.strip())
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def parse_datetime(value: Any):
    """Convierte fechas ISO o Unix a datetime con zona Europe/Madrid."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            stamp = float(value)
            if stamp > 100000000000:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(MADRID)

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if re.fullmatch(r"\d{10,13}", text):
                return parse_datetime(float(text))
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=MADRID)
            return parsed.astimezone(MADRID)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return None


def datetime_output(value: Any):
    parsed = parse_datetime(value)
    return parsed.isoformat() if parsed else None


def get_json(url: str):
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def normalize_train_code(value):
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).upper()


def fold_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).strip().casefold()


def community_values(raw: dict, normalized: dict) -> list[str]:
    direct = first_present(raw, (
        "autonomousCommunities", "autonomousCommunity", "community",
        "region", "comunidadAutonoma", "ccaa"
    ))
    if isinstance(direct, (list, tuple, set)):
        found = [str(x).strip() for x in direct if x]
        if found:
            return list(dict.fromkeys(found))
    elif direct:
        return [str(direct).strip()]

    text = " ".join(
        str(normalized.get(k) or "")
        for k in ("originName", "destinationName", "nextStationName")
    ).casefold()

    return [
        community for community, pattern in CITY_PATTERNS.items()
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]


def transport_type(train: dict) -> str:
    text = " ".join(str(train.get(k) or "") for k in (
        "productName", "productCategory", "trafficType", "providerProduct", "type"
    )).casefold()

    if "cercan" in text or "rodal" in text or "commuter" in text:
        return "Cercanías"
    if "media distancia" in text or "regional" in text:
        return "Media Distancia"
    if "ave" in text or "alta velocidad" in text or "high_speed" in text:
        return "Alta Velocidad"
    if "intercity" in text or "larga distancia" in text or "long_distance" in text:
        return "Larga Distancia"
    if "avant" in text:
        return "Avant"
    if text.strip():
        return str(train.get("productName") or train.get("type") or "Otros")
    return "Sin clasificar"


def normalize_train(item: Any):
    if not isinstance(item, dict):
        return None

    result = dict(item)
    for target, aliases in FIELD_ALIASES.items():
        value = first_present(item, aliases)
        if value is not None:
            result[target] = value

    result["delayMinutes"] = number_or_none(result.get("delayMinutes"))
    result["speedKmh"] = number_or_none(result.get("speedKmh"))
    result["speed"] = result["speedKmh"]
    result["speedSource"] = "real" if result["speedKmh"] is not None else None

    lat = number_or_none(result.get("latitude"))
    lon = number_or_none(result.get("longitude"))
    result["latitude"] = lat if lat is not None and -90 <= lat <= 90 else None
    result["longitude"] = lon if lon is not None and -180 <= lon <= 180 else None

    communities = community_values(item, result)
    result["autonomousCommunities"] = communities
    result["autonomousCommunity"] = communities[0] if len(communities) == 1 else None

    result["expectedArrival"] = datetime_output(result.get("expectedArrival"))
    result["estimatedArrival"] = datetime_output(result.get("estimatedArrival"))
    result["nextStationExpectedArrival"] = datetime_output(result.get("nextStationExpectedArrival"))
    result["timestamp"] = datetime_output(result.get("timestamp"))
    result["transportType"] = transport_type(result)
    result.setdefault("currentLocation", None)
    return result


# ============================================================
# ITINERARIOS DE RADAR DE TRENES
# ============================================================

def fetch_train_details(train_id):
    now = time()
    with cache_lock:
        cached = detail_cache.get(train_id)
        if cached and now - cached["time"] < DETAIL_CACHE_SECONDS:
            return cached["data"]

    url = f"{TRAIN_API_BASE}/trains/{quote(str(train_id), safe='')}/stops"
    try:
        data = get_json(url)
        if not isinstance(data, dict):
            return {}
        detail = data.get("detail") or data
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
                    "arrivalTime": datetime_output(stop.get("arrivalTime")),
                    "arrivalDelay": number_or_none(stop.get("arrivalDelay")),
                    "departureTime": datetime_output(stop.get("departureTime")),
                    "departureDelay": number_or_none(stop.get("departureDelay"))
                })

        with cache_lock:
            detail_cache[train_id] = {"time": time(), "data": result}
        return result

    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        log.warning("No se pudo consultar el itinerario %s: %s", train_id, error)
        return {}


def enrich_train(train, detail):
    if not detail:
        return train

    operator_code = detail.get("operatorCode")
    if operator_code:
        train["operatorCode"] = operator_code
        if not train.get("operator"):
            # Es un código del proveedor, no una marca comercial confirmada.
            train["operator"] = operator_code

    if detail.get("product"):
        train["providerProduct"] = detail["product"]
    if detail.get("trafficType"):
        train["trafficType"] = detail["trafficType"]

    stops = detail.get("stops", [])
    train["stops"] = stops

    # Hora de llegada final: buscamos la estación de destino en el itinerario.
    # Si el nombre no coincide exactamente, utilizamos la última parada con llegada.
    destination = fold_text(train.get("destinationName"))
    target_stop = None

    if destination:
        target_stop = next(
            (s for s in stops if fold_text(s.get("stationName")) == destination),
            None
        )

    if target_stop is None:
        target_stop = next(
            (s for s in reversed(stops) if s.get("arrivalTime")),
            None
        )

    if target_stop:
        scheduled = parse_datetime(target_stop.get("arrivalTime"))
        delay = number_or_none(target_stop.get("arrivalDelay"))
        if scheduled:
            train["expectedArrival"] = scheduled.isoformat()
            if delay is not None:
                estimated = scheduled.timestamp() + delay * 60
                train["estimatedArrival"] = datetime.fromtimestamp(
                    estimated, tz=timezone.utc
                ).astimezone(MADRID).isoformat()

    # Guardamos por separado la hora de la próxima estación para no confundirla
    # con la llegada final al destino.
    next_station = fold_text(train.get("nextStationName"))
    if next_station:
        next_stop = next(
            (s for s in stops if fold_text(s.get("stationName")) == next_station),
            None
        )
        if next_stop and next_stop.get("arrivalTime"):
            train["nextStationExpectedArrival"] = next_stop["arrivalTime"]
            if next_stop.get("arrivalDelay") is not None:
                planned_next = parse_datetime(next_stop["arrivalTime"])
                if planned_next:
                    estimate_next = planned_next.timestamp() + float(next_stop["arrivalDelay"]) * 60
                    train["nextStationEstimatedArrival"] = datetime.fromtimestamp(
                        estimate_next, tz=timezone.utc
                    ).astimezone(MADRID).isoformat()

    return train


# ============================================================
# POSICIONES Y ACTUALIZACIONES GTFS-RT DE RENFE
# ============================================================

def fetch_gtfs_realtime():
    results = {}
    urls = {
        "ld_positions": GTFS_LD_POSITIONS,
        "ld_updates": GTFS_LD_UPDATES,
        "commuter_positions": GTFS_CERCANIAS_POSITIONS,
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(get_json, url): key for key, url in urls.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as error:
                log.warning("Feed GTFS-RT %s no disponible: %s", key, error)
                results[key] = {}
    return results


def feed_header_datetime(feed):
    try:
        stamp = feed.get("header", {}).get("timestamp")
        return datetime_output(stamp)
    except (AttributeError, TypeError):
        return None


def parse_gtfs_entities(feeds):
    updates_by_trip = {}
    for entity in feeds.get("ld_updates", {}).get("entity", []):
        update = entity.get("tripUpdate", {}) if isinstance(entity, dict) else {}
        trip = update.get("trip", {})
        trip_id = trip.get("tripId")
        if trip_id:
            updates_by_trip[str(trip_id)] = update

    candidates = []

    for feed_name in ("ld_positions", "commuter_positions"):
        feed = feeds.get(feed_name, {})
        feed_timestamp = feed_header_datetime(feed)

        for entity in feed.get("entity", []) if isinstance(feed, dict) else []:
            vehicle = entity.get("vehicle", {}) if isinstance(entity, dict) else {}
            if not isinstance(vehicle, dict):
                continue

            trip = vehicle.get("trip", {}) or {}
            vehicle_info = vehicle.get("vehicle", {}) or {}
            position = vehicle.get("position", {}) or {}

            label = vehicle_info.get("label") or vehicle_info.get("id")
            if not label:
                continue

            lat = number_or_none(position.get("latitude"))
            lon = number_or_none(position.get("longitude"))
            if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue

            trip_id = str(trip.get("tripId") or "")
            update = updates_by_trip.get(trip_id, {})
            stop_updates = update.get("stopTimeUpdate", []) or []
            predicted_arrival = None
            update_stop_id = None

            if stop_updates:
                # La actualización suele referirse a una parada concreta;
                # no se confunde con la llegada final del tren.
                stop_update = stop_updates[0]
                update_stop_id = stop_update.get("stopId")
                arrival = stop_update.get("arrival", {}) or {}
                predicted_arrival = datetime_output(arrival.get("time"))

            delay_seconds = number_or_none(update.get("delay"))
            if delay_seconds is None and stop_updates:
                delay_seconds = number_or_none(
                    (stop_updates[0].get("arrival") or {}).get("delay")
                )

            status = vehicle.get("currentStatus")
            stop_id = vehicle.get("stopId")
            timestamp = datetime_output(vehicle.get("timestamp")) or feed_timestamp

            candidates.append({
                "trainCode": str(label).strip(),
                "vehicleId": str(vehicle_info.get("id") or label),
                "tripId": trip_id,
                "latitude": lat,
                "longitude": lon,
                "currentStatus": status,
                "currentStopId": str(stop_id) if stop_id is not None else None,
                "gtfsTimestamp": timestamp,
                "gtfsDelayMinutes": (
                    round(delay_seconds / 60, 1)
                    if delay_seconds is not None else None
                ),
                "gtfsPredictedStopArrival": predicted_arrival,
                "gtfsUpdateStopId": str(update_stop_id) if update_stop_id is not None else None,
                "source": "Renfe GTFS-RT",
                "feedType": "Cercanías" if feed_name == "commuter_positions" else "Larga Distancia"
            })

    # Un vehículo puede aparecer varias veces por variantes de tripId.
    # Priorizamos la entrada con parada y actualización de horario.
    selected = {}
    for candidate in candidates:
        key = normalize_train_code(candidate["trainCode"])
        score = (
            2 if candidate.get("currentStopId") else 0
        ) + (
            1 if candidate.get("gtfsDelayMinutes") is not None else 0
        ) + (
            1 if candidate.get("gtfsPredictedStopArrival") else 0
        )
        previous = selected.get(key)
        if previous is None or score > previous[0]:
            selected[key] = (score, candidate)

    return [item[1] for item in selected.values()]


def merge_gtfs_position(train, position):
    train["latitude"] = position["latitude"]
    train["longitude"] = position["longitude"]
    train["gtfsTripId"] = position.get("tripId")
    train["currentStatus"] = position.get("currentStatus")
    train["currentStopId"] = position.get("currentStopId")
    train["positionSource"] = "Renfe GTFS-RT"
    train["timestamp"] = position.get("gtfsTimestamp") or train.get("timestamp")

    # El retraso global del viaje GTFS-RT es preferible al valor de la
    # posición de Radar, si el feed lo proporciona.
    if position.get("gtfsDelayMinutes") is not None:
        train["delayMinutes"] = position["gtfsDelayMinutes"]

    station_name = train.get("currentStationName")
    status = position.get("currentStatus")
    stop_id = position.get("currentStopId")

    # Si el código GTFS coincide con el código de parada del itinerario,
    # podemos mostrar el nombre de la estación actual.
    if stop_id and not station_name:
        current_stop = next(
            (s for s in train.get("stops", [])
             if str(s.get("stationCode") or "") == str(stop_id)),
            None
        )
        if current_stop:
            station_name = current_stop.get("stationName")
            if status == "STOPPED_AT":
                train["currentStationName"] = station_name
            elif status == "IN_TRANSIT_TO":
                train["nextStationName"] = station_name
        station_name = train.get("currentStationName") or station_name

    # Si conocemos la hora programada de destino y GTFS aporta un retraso
    # global, calculamos una estimación de llegada sin cambiar la programada.
    update_stop_id = position.get("gtfsUpdateStopId")
    destination_stop = next(
        (s for s in train.get("stops", [])
         if str(s.get("stationCode") or "") == str(update_stop_id)),
        None
    ) if update_stop_id else None

    # Si la actualización GTFS corresponde al destino, su hora prevista
    # prevalece sobre la estimación calculada.
    if destination_stop and position.get("gtfsPredictedStopArrival"):
        train["estimatedArrival"] = position["gtfsPredictedStopArrival"]

    if (train.get("expectedArrival") and train.get("estimatedArrival") is None
            and position.get("gtfsDelayMinutes") is not None):
        scheduled = parse_datetime(train["expectedArrival"])
        if scheduled:
            estimated = scheduled.timestamp() + position["gtfsDelayMinutes"] * 60
            train["estimatedArrival"] = datetime.fromtimestamp(
                estimated, tz=timezone.utc
            ).astimezone(MADRID).isoformat()

    if station_name:
        train["currentLocation"] = station_name
    elif status == "STOPPED_AT":
        train["currentLocation"] = (
            f"Detenido en parada · código {stop_id}" if stop_id
            else "Detenido en una parada"
        )
    elif status == "IN_TRANSIT_TO":
        next_station = train.get("nextStationName")
        train["currentLocation"] = (
            f"En circulación · hacia {next_station}"
            if next_station else "En circulación · posición GPS"
        )
    else:
        train["currentLocation"] = "Posición GPS disponible"

    if position.get("gtfsPredictedStopArrival"):
        train["nextStopEstimatedArrival"] = position["gtfsPredictedStopArrival"]
    if position.get("gtfsUpdateStopId"):
        train["gtfsUpdateStopId"] = position["gtfsUpdateStopId"]

    return train


# ============================================================
# VELOCIDAD ESTIMADA A PARTIR DE POSICIONES SUCESIVAS
# ============================================================

def haversine_km(lat1, lon1, lat2, lon2):
    earth_radius = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * earth_radius * math.asin(math.sqrt(a))


def estimate_speed(train):
    lat, lon = train.get("latitude"), train.get("longitude")
    if lat is None or lon is None or train.get("stale") is True:
        return None

    train_id = normalize_train_code(train.get("trainCode") or train.get("id"))
    if not train_id:
        return None

    # El timestamp del feed puede venir vacío o no cambiar entre consultas.
    # Para comparar observaciones locales utilizamos el reloj del servidor.
    current_timestamp = time()

    with cache_lock:
        previous = position_history.get(train_id)
        position_history[train_id] = {
            "lat": lat, "lon": lon, "timestamp": current_timestamp
        }

    if not previous:
        return None

    elapsed = current_timestamp - previous["timestamp"]
    if elapsed < 20 or elapsed > 1800:
        return None

    distance = haversine_km(previous["lat"], previous["lon"], lat, lon)
    if distance < 0.03:
        return None

    speed = distance / (elapsed / 3600)
    if speed < 1 or speed > 350:
        return None
    return round(speed, 1)


# ============================================================
# API
# ============================================================

@app.route("/")
def index():
    if (FRONTEND_DIST / "index.html").is_file():
        return send_from_directory(FRONTEND_DIST, "index.html")
    return render_template("index.html")


@app.route("/assets/<path:filename>")
def frontend_asset(filename):
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.route("/api/trains")
def get_trains():
    try:
        try:
            data = get_json(ADIF_API)
        except requests.RequestException as error:
            log.warning("Radar de Trenes no disponible; se intentará continuar con GTFS-RT: %s", error)
            data = []

        if isinstance(data, list):
            raw_trains = data
        elif isinstance(data, dict):
            raw_trains = data.get("trains")
            if raw_trains is None:
                raw_trains = data.get("fleet", data.get("data", []))
        else:
            raw_trains = []

        if isinstance(raw_trains, dict):
            raw_trains = list(raw_trains.values())
        if not isinstance(raw_trains, list):
            raw_trains = []

        trains = []
        for item in raw_trains:
            normalized = normalize_train(item)
            if normalized:
                trains.append(normalized)

        # Enriquecemos con los itinerarios de Radar de Trenes.
        futures = {}
        for train in trains:
            train_id = train.get("id") or train.get("trainCode")
            if train_id:
                futures[executor.submit(fetch_train_details, str(train_id))] = train

        for future in as_completed(futures):
            train = futures[future]
            try:
                enrich_train(train, future.result())
            except Exception:
                log.exception("Error enriqueciendo tren %s", train.get("trainCode"))

        # Incorporamos las posiciones oficiales GTFS-RT de Renfe.
        feeds = fetch_gtfs_realtime()
        gtfs_positions = parse_gtfs_entities(feeds)

        by_code = {
            normalize_train_code(t.get("trainCode")): t
            for t in trains if t.get("trainCode")
        }

        for position in gtfs_positions:
            code = normalize_train_code(position.get("trainCode"))
            train = by_code.get(code)

            if train is None:
                # Se añade el tren oficial aunque no aparezca en Radar.
                train = {
                    "id": f"renfe-{code}",
                    "trainCode": position["trainCode"],
                    "productName": (
                        "Cercanías" if position["feedType"] == "Cercanías"
                        else "Larga Distancia"
                    ),
                    "operator": "Renfe",
                    "originName": None,
                    "destinationName": None,
                    "delayMinutes": position.get("gtfsDelayMinutes"),
                    "speedKmh": None,
                    "speed": None,
                    "expectedArrival": None,
                    "estimatedArrival": None,
                    "autonomousCommunities": [],
                    "stops": [],
                }
                trains.append(train)
                by_code[code] = train

            merge_gtfs_position(train, position)

        # Recalcula etiquetas y comunidades después de combinar las fuentes.
        for train in trains:
            if not train.get("currentLocation"):
                train["currentLocation"] = (
                    "Posición GPS disponible"
                    if train.get("latitude") is not None
                    else "Ubicación no disponible"
                )

            communities = community_values(train, train)
            train["autonomousCommunities"] = communities
            train["autonomousCommunity"] = communities[0] if len(communities) == 1 else None
            train["transportType"] = transport_type(train)

            if train.get("speedKmh") is None:
                estimated_speed = estimate_speed(train)
                train["speedKmh"] = estimated_speed
                train["speed"] = estimated_speed
                train["speedSource"] = "estimated" if estimated_speed is not None else None

        with_speed = sum(1 for t in trains if t.get("speedKmh") is not None)
        with_operator = sum(1 for t in trains if t.get("operator"))
        with_stops = sum(1 for t in trains if t.get("stops"))
        with_position = sum(
            1 for t in trains
            if t.get("latitude") is not None and t.get("longitude") is not None
        )

        return jsonify({
            "success": True,
            "total": len(trains),
            "updated": datetime.now(MADRID).isoformat(),
            "timezone": "Europe/Madrid",
            "refreshSeconds": REFRESH_HINT_SECONDS,
            "coverage": {
                "withSpeed": with_speed,
                "withOperator": with_operator,
                "withStops": with_stops,
                "withPosition": with_position,
                "gtfsVehicles": len(gtfs_positions),
            },
            "sources": {
                "fleet": "Radar de Trenes",
                "positions": "Renfe GTFS-RT",
                "tripUpdates": "Renfe GTFS-RT"
            },
            "trains": trains
        })

    except requests.Timeout:
        log.warning("Timeout consultando fuentes ferroviarias")
        return jsonify({
            "success": False,
            "error": "Tiempo de espera agotado al consultar las fuentes ferroviarias",
            "trains": []
        }), 504

    except requests.RequestException as error:
        log.exception("Error de conexión con una fuente ferroviaria: %s", error)
        return jsonify({
            "success": False,
            "error": "No se ha podido consultar la fuente ferroviaria principal",
            "trains": []
        }), 502

    except (ValueError, TypeError) as error:
        log.exception("Respuesta con formato inesperado: %s", error)
        return jsonify({
            "success": False,
            "error": "Una fuente ha devuelto datos con formato inesperado",
            "trains": []
        }), 502


@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "application": "ADIF Rail Monitor",
        "port": PORT,
        "timezone": "Europe/Madrid",
        "sources": ["Radar de Trenes", "Renfe GTFS-RT"],
        "detailCacheSeconds": DETAIL_CACHE_SECONDS
    })


if __name__ == "__main__":
    print("=" * 52)
    print("             ADIF RAIL MONITOR")
    print("=" * 52)
    print(f"Servidor: http://127.0.0.1:{PORT}")
    print("Zona horaria: Europe/Madrid")
    print("=" * 52)

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
