"""Servizio isolato per il Percorso giornaliero.

Non tocca in alcun modo la logica di calendario/pratiche/pagamenti: legge le
tappe gia' individuate da calendar_service.route_eligible_events e si occupa
solo di finestre orarie, geocodifica, ottimizzazione dell'ordine (Google
Routes API quando configurata, altrimenti un ordine per vicinanza calcolato
localmente) e costruzione degli URL verso Google Maps.

La funzione deve restare utilizzabile anche senza alcuna chiave Google
configurata, con quota esaurita o con l'API irraggiungibile: ogni chiamata
esterna e' avvolta in try/except e non propaga mai un'eccezione al
chiamante, esattamente come il precedente gia' in uso in app.py
(api_zip_lookup, che chiama Nominatim con lo stesso stile)."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROME_TZ = ZoneInfo("Europe/Rome")

# Sosta di default (minuti) per tipo di luogo quando non c'e' ne' un valore
# specifico sul veterinario ne' una chiave in settings.
DEFAULT_SERVICE_MINUTES = {
    "Veterinario": 10, "Privato": 10, "Sede Livorno": 5, "Sede Empoli": 5,
    "Altro indirizzo": 10,
}
SETTINGS_KEY_BY_LOCATION_TYPE = {
    "Veterinario": "route_service_minutes_veterinario",
    "Privato": "route_service_minutes_privato",
    "Sede Livorno": "route_service_minutes_sede",
    "Sede Empoli": "route_service_minutes_sede",
}

# Numero massimo di tappe (origine+destinazione+intermedie) trasferibili in
# un solo URL "https://www.google.com/maps/dir/?api=1..."; oltre questo
# limite il percorso viene diviso in piu' sezioni, mai troncato in silenzio.
GOOGLE_MAPS_URL_WAYPOINT_LIMIT = 23

ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


class RouteApiError(Exception):
    pass


def rome_now() -> datetime:
    return datetime.now(ROME_TZ).replace(tzinfo=None)


def service_duration_minutes(conn, location_type, veterinarian_row=None):
    """Durata di sosta stimata per una tappa: priorita' al valore specifico
    del veterinario, poi al default configurabile in settings, poi al
    default fisso per tipo di luogo."""
    if veterinarian_row is not None and veterinarian_row["service_duration_minutes"]:
        return int(veterinarian_row["service_duration_minutes"])
    key = SETTINGS_KEY_BY_LOCATION_TYPE.get(location_type, "route_service_minutes_altro")
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            pass
    return DEFAULT_SERVICE_MINUTES.get(location_type, 10)


def time_windows_for_stop(conn, event_row):
    """Finestre orarie da rispettare per una tappa, in ordine di priorita':
    1) start_at/end_at dell'evento stesso, quando rappresentano un vincolo
       reale (finestra piu' stretta di 6 ore) e non un semplice placeholder
       giornaliero;
    2) orario del veterinario salvato manualmente per quel giorno della
       settimana (veterinarian_hours);
    3) nessun vincolo.
    Ritorna (finestre, source): finestre e' None (nessun vincolo), [] (chiuso
    quel giorno) oppure una lista di tuple ('HH:MM','HH:MM'); source e' una
    fra 'evento','veterinario','veterinario_chiuso','nessuno'."""
    start_at = event_row["start_at"] or ""
    end_at = event_row["end_at"] or ""
    start_time, end_time = start_at[11:16], end_at[11:16]
    if start_time and end_time and not event_row["all_day"]:
        try:
            span = (datetime.strptime(end_time, "%H:%M") - datetime.strptime(start_time, "%H:%M")).total_seconds() / 60
            if 0 < span <= 360:
                return [(start_time, end_time)], "evento"
        except ValueError:
            pass
    if event_row["veterinarian_id"] and start_at[:10]:
        weekday = date.fromisoformat(start_at[:10]).weekday()
        hours = conn.execute(
            "SELECT * FROM veterinarian_hours WHERE veterinarian_id=? AND day_of_week=?",
            (event_row["veterinarian_id"], weekday),
        ).fetchone()
        if hours:
            if hours["closed"]:
                return [], "veterinario_chiuso"
            windows = [
                (hours[a], hours[b])
                for a, b in (("morning_start", "morning_end"), ("afternoon_start", "afternoon_end"))
                if hours[a] and hours[b]
            ]
            if windows:
                return windows, "veterinario"
    return None, "nessuno"


def validate_arrival(arrival_time, windows, source):
    """Confronta un orario di arrivo stimato ('HH:MM') con le finestre
    orarie note. Ritorna (stato, messaggio): stato in
    verde/ambra/rosso/grigio/blu, messaggio sempre presente (mai solo
    colore, per accessibilita')."""
    if windows is None:
        return "grigio", "Nessun vincolo orario"
    if not windows:
        return "rosso", "Struttura chiusa in questo giorno"
    windows_text = "; ".join(f"{s}–{e}" for s, e in windows)
    if any(start <= arrival_time <= end for start, end in windows):
        if source == "evento":
            return "blu", f"Orario concordato rispettato ({windows_text})"
        return "verde", f"Aperto {windows_text}"
    upcoming = [start for start, _ in windows if start > arrival_time]
    if upcoming:
        next_start = min(upcoming)
        wait_minutes = (datetime.strptime(next_start, "%H:%M") - datetime.strptime(arrival_time, "%H:%M")).total_seconds() / 60
        if wait_minutes <= 30:
            return "ambra", f"Attesa di {int(wait_minutes)} min, apertura alle {next_start}"
    return "rosso", f"Arrivo previsto {arrival_time}, fuori orario ({windows_text})"


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_neighbor_order(start, stops):
    """Fallback locale, zero dipendenze esterne: ad ogni passo sceglie la
    tappa piu' vicina in linea d'aria alla posizione corrente. Usato quando
    Google Routes API non e' configurata, non risponde o e' fuori quota —
    la funzione deve continuare a produrre un ordine utile in ogni caso."""
    remaining = list(stops)
    order = []
    current = start
    while remaining:
        remaining.sort(key=lambda s: haversine_km(current["lat"], current["lng"], s["lat"], s["lng"]))
        nxt = remaining.pop(0)
        order.append(nxt)
        current = nxt
    return order


def _parse_duration_seconds(value):
    try:
        return int(str(value).rstrip("s"))
    except (TypeError, ValueError):
        return 0


def compute_route_google(api_key, origin, destination, waypoints, traffic_aware=True):
    """Chiama Google Routes API (computeRoutes) con optimizeWaypointOrder.
    origin/destination/waypoints sono dict con 'lat'/'lng'. Solleva
    RouteApiError in caso di problema — il chiamante ricade sempre sul
    fallback locale, non deve mai propagarsi come errore bloccante."""
    body = {
        "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
        "destination": {"location": {"latLng": {"latitude": destination["lat"], "longitude": destination["lng"]}}},
        "intermediates": [
            {"location": {"latLng": {"latitude": w["lat"], "longitude": w["lng"]}}} for w in waypoints
        ],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE" if traffic_aware else "TRAFFIC_UNAWARE",
        "optimizeWaypointOrder": True,
    }
    req = urllib.request.Request(
        ROUTES_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "routes.optimizedIntermediateWaypointIndex,routes.legs.distanceMeters,"
                "routes.legs.duration,routes.distanceMeters,routes.duration"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RouteApiError(f"Google Routes API non raggiungibile: {exc}") from exc
    routes = payload.get("routes") or []
    if not routes:
        raise RouteApiError("Google Routes API non ha restituito alcun percorso")
    route = routes[0]
    legs = route.get("legs") or []
    return {
        "order": route.get("optimizedIntermediateWaypointIndex", list(range(len(waypoints)))),
        "legs": [
            {
                "distance_meters": leg.get("distanceMeters", 0),
                "duration_seconds": _parse_duration_seconds(leg.get("duration", "0s")),
            }
            for leg in legs
        ],
        "total_distance_meters": route.get("distanceMeters", 0),
        "total_duration_seconds": _parse_duration_seconds(route.get("duration", "0s")),
    }


def optimize_route(start, destination, stops, mode="veloce", api_key=None):
    """Ordina le tappe partendo da 'start' verso 'destination'. Usa Google
    Routes API se e' configurata una chiave e tutte le tappe hanno
    coordinate; in ogni altro caso (chiave assente, errore, quota, timeout,
    coordinate mancanti) ricade sul fallback per vicinanza — non blocca mai
    la creazione del percorso. Ritorna (tappe_ordinate, dettagli_google_o_None,
    sorgente) con sorgente in 'google'|'vicinanza'|'nessuna'."""
    api_key = api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    geocoded = [s for s in stops if s.get("lat") is not None and s.get("lng") is not None]
    has_coords = bool(geocoded) and start.get("lat") is not None and destination.get("lat") is not None
    if api_key and has_coords and len(geocoded) == len(stops):
        try:
            result = compute_route_google(api_key, start, destination, geocoded, traffic_aware=(mode == "veloce"))
            ordered = [geocoded[i] for i in result["order"]]
            return ordered, result, "google"
        except Exception as exc:
            print(f"[ROUTE] Google Routes API non disponibile, uso ordine per vicinanza: {type(exc).__name__}: {exc}", flush=True)
    if has_coords:
        return nearest_neighbor_order(start, geocoded), None, "vicinanza"
    return list(stops), None, "nessuna"


def geocode_address(address, api_key=None):
    """Geocodifica un indirizzo: tenta prima Nominatim/OpenStreetMap (gratuito,
    stesso servizio gia' usato in app.py per il recupero del CAP), poi Google
    Geocoding se una chiave e' configurata. Non solleva mai eccezioni: in
    caso di fallimento ritorna (None, None) e la tappa resta senza
    coordinate (nessun vincolo, mai un blocco)."""
    address = (address or "").strip()
    if not address:
        return None, None
    try:
        params = urllib.parse.urlencode({"q": address, "format": "jsonv2", "limit": "1"})
        req = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={"Accept": "application/json", "User-Agent": "PetParadiseManager/1.0 (route geocoding)"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        if payload:
            return float(payload[0]["lat"]), float(payload[0]["lon"])
    except Exception as exc:
        print(f"[ROUTE] Nominatim non disponibile: {type(exc).__name__}: {exc}", flush=True)
    api_key = api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if api_key:
        try:
            params = urllib.parse.urlencode({"address": address, "key": api_key})
            req = urllib.request.Request(f"https://maps.googleapis.com/maps/api/geocode/json?{params}", method="GET")
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            results = payload.get("results") or []
            if results:
                loc = results[0]["geometry"]["location"]
                return float(loc["lat"]), float(loc["lng"])
        except Exception as exc:
            print(f"[ROUTE] Google Geocoding non disponibile: {type(exc).__name__}: {exc}", flush=True)
    return None, None


def resolve_coordinates(conn, address, veterinarian_row=None, api_key=None):
    """Coordinate per un indirizzo, con cache. Priorita' alle coordinate gia'
    salvate sul veterinario (colonne lat/lng aggiunte a veterinarians),
    altrimenti cache generica per indirizzo in geocode_cache, altrimenti
    nuova geocodifica (Nominatim poi Google) salvata in cache. Non chiama mai
    l'API per lo stesso indirizzo invariato una seconda volta."""
    if veterinarian_row is not None and veterinarian_row["lat"] is not None and veterinarian_row["lng"] is not None:
        return veterinarian_row["lat"], veterinarian_row["lng"]
    address = (address or "").strip()
    if not address:
        return None, None
    cached = conn.execute("SELECT lat,lng FROM geocode_cache WHERE address=?", (address,)).fetchone()
    if cached:
        return cached["lat"], cached["lng"]
    lat, lng = geocode_address(address, api_key=api_key)
    if lat is not None and lng is not None:
        stamp = rome_now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO geocode_cache(address,lat,lng,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(address) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,updated_at=excluded.updated_at",
            (address, lat, lng, stamp),
        )
    return lat, lng


def _single_maps_url(origin, destination, waypoints):
    params = {"api": "1", "travelmode": "driving", "origin": origin, "destination": destination}
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)


def build_maps_urls(origin, destination, waypoints, limit=GOOGLE_MAPS_URL_WAYPOINT_LIMIT):
    """Costruisce uno o piu' URL Google Maps (stesso schema gia' usato dai
    pulsanti "Naviga" esistenti, solo con origine+tappe intermedie in piu').
    Se le tappe superano il limite trasferibile in un URL, il percorso viene
    diviso in piu' sezioni consecutive (mai una tappa eliminata in silenzio);
    ogni sezione successiva riparte dall'ultima tappa di quella precedente."""
    if not waypoints:
        return [_single_maps_url(origin, destination, [])]
    urls = []
    remaining = list(waypoints)
    section_origin = origin
    while remaining:
        take = remaining[:limit]
        remaining = remaining[limit:]
        if remaining:
            section_destination = take[-1]
            section_waypoints = take[:-1]
        else:
            section_destination = destination
            section_waypoints = take
        urls.append(_single_maps_url(section_origin, section_destination, section_waypoints))
        section_origin = section_destination
    return urls
