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
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROME_TZ = ZoneInfo("Europe/Rome")

# Sigla provincia (come gia' salvata in practices.owner_province/
# clients.province) -> nome esteso, per confrontare la provincia attesa con
# quella restituita dal geocoding (Nominatim/Google restituiscono sempre il
# nome esteso, mai la sigla). Elenco completo delle province italiane
# (richiesta esplicita dell'utente: la provincia va verificata quando
# disponibile, non solo per la Toscana).
PROVINCE_NAMES = {
    "AG":"Agrigento","AL":"Alessandria","AN":"Ancona","AO":"Aosta","AR":"Arezzo","AP":"Ascoli Piceno",
    "AT":"Asti","AV":"Avellino","BA":"Bari","BT":"Barletta-Andria-Trani","BL":"Belluno","BN":"Benevento",
    "BG":"Bergamo","BI":"Biella","BO":"Bologna","BZ":"Bolzano","BS":"Brescia","BR":"Brindisi","CA":"Cagliari",
    "CL":"Caltanissetta","CB":"Campobasso","CE":"Caserta","CT":"Catania","CZ":"Catanzaro","CH":"Chieti",
    "CO":"Como","CS":"Cosenza","CR":"Cremona","KR":"Crotone","CN":"Cuneo","EN":"Enna","FM":"Fermo",
    "FE":"Ferrara","FI":"Firenze","FG":"Foggia","FC":"Forli'-Cesena","FR":"Frosinone","GE":"Genova",
    "GO":"Gorizia","GR":"Grosseto","IM":"Imperia","IS":"Isernia","SP":"La Spezia","AQ":"L'Aquila",
    "LT":"Latina","LE":"Lecce","LC":"Lecco","LI":"Livorno","LO":"Lodi","LU":"Lucca","MC":"Macerata",
    "MN":"Mantova","MS":"Massa-Carrara","MT":"Matera","ME":"Messina","MI":"Milano","MO":"Modena",
    "MB":"Monza e della Brianza","NA":"Napoli","NO":"Novara","NU":"Nuoro","OR":"Oristano","PD":"Padova",
    "PA":"Palermo","PR":"Parma","PV":"Pavia","PG":"Perugia","PU":"Pesaro e Urbino","PE":"Pescara",
    "PC":"Piacenza","PI":"Pisa","PT":"Pistoia","PN":"Pordenone","PZ":"Potenza","PO":"Prato","RG":"Ragusa",
    "RA":"Ravenna","RC":"Reggio Calabria","RE":"Reggio Emilia","RI":"Rieti","RN":"Rimini","RM":"Roma",
    "RO":"Rovigo","SA":"Salerno","SS":"Sassari","SV":"Savona","SI":"Siena","SR":"Siracusa","SO":"Sondrio",
    "SU":"Sud Sardegna","TA":"Taranto","TE":"Teramo","TR":"Terni","TO":"Torino","TP":"Trapani","TN":"Trento",
    "TV":"Treviso","TS":"Trieste","UD":"Udine","VA":"Varese","VE":"Venezia","VB":"Verbano-Cusio-Ossola",
    "VC":"Vercelli","VR":"Verona","VV":"Vibo Valentia","VI":"Vicenza","VT":"Viterbo",
}

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
ROUTE_MATRIX_API_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
PLACE_DETAILS_API_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Dopo quanti giorni un orario recuperato da Google Places va considerato
# scaduto e ricontrollato automaticamente (chiave di settings sovrascrivibile:
# route_google_hours_ttl_days). Non si applica mai agli orari inseriti a mano
# (hours_source='manuale'): quelli non vengono mai ricontrollati/sovrascritti
# automaticamente, per priorita' esplicita richiesta dall'utente.
DEFAULT_GOOGLE_HOURS_TTL_DAYS = 30


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


def compute_route_matrix_google(api_key, points, traffic_aware=False):
    """Distanze e durate reali (strada, non linea d'aria) fra ogni coppia di
    punti tramite Google Routes API (computeRouteMatrix), in un'unica
    chiamata. 'points' e' una lista di dict {'lat':,'lng':}; l'indice nella
    lista e' l'indice usato nella matrice ritornata: {(i,j):{'distance_meters':,
    'duration_seconds':}}. Solleva RouteApiError in caso di problema — il
    chiamante deve sempre ricadere sul fallback locale, mai propagare."""
    if len(points) < 2:
        return {}
    waypoints = [{"waypoint": {"location": {"latLng": {"latitude": p["lat"], "longitude": p["lng"]}}}} for p in points]
    body = {
        "origins": waypoints,
        "destinations": waypoints,
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE" if traffic_aware else "TRAFFIC_UNAWARE",
    }
    req = urllib.request.Request(
        ROUTE_MATRIX_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,duration,condition",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RouteApiError(f"Google Route Matrix API non raggiungibile: {exc}") from exc
    elements = payload if isinstance(payload, list) else payload.get("elements", [])
    matrix = {}
    for element in elements:
        if element.get("condition") not in (None, "ROUTE_EXISTS"):
            continue
        i, j = element.get("originIndex", 0), element.get("destinationIndex", 0)
        if i == j:
            continue
        matrix[(i, j)] = {
            "distance_meters": element.get("distanceMeters", 0),
            "duration_seconds": _parse_duration_seconds(element.get("duration", "0s")),
        }
    return matrix


def nearest_neighbor_order_matrix(start_index, stop_indices, matrix):
    """Come nearest_neighbor_order ma su indici e distanze reali (matrix),
    non su haversine: usato per 'piu' breve' quando Google e' configurato."""
    remaining = list(stop_indices)
    order = []
    current = start_index
    while remaining:
        remaining.sort(key=lambda idx: matrix.get((current, idx), {}).get("distance_meters", float("inf")))
        nxt = remaining.pop(0)
        order.append(nxt)
        current = nxt
    return order


def _tour_distance(start_index, end_index, order, matrix):
    points = [start_index] + list(order) + [end_index]
    total = 0
    for a, b in zip(points, points[1:]):
        total += matrix.get((a, b), {}).get("distance_meters", 0) or 0
    return total


def two_opt_improve(start_index, end_index, order, matrix, max_iterations=200):
    """Ricerca locale 2-opt standard sulla distanza reale totale (partenza e
    arrivo fissi, le tappe intermedie possono essere riordinate). Il numero
    di tappe di una giornata reale e' piccolo (tipicamente <25), quindi un
    2-opt limitato a max_iterations e' rapido e non richiede altre chiamate
    API: lavora sulla matrice gia' calcolata."""
    best = list(order)
    best_cost = _tour_distance(start_index, end_index, best, matrix)
    n = len(best)
    iterations = 0
    improved = True
    while improved and n >= 2 and iterations < max_iterations:
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                candidate = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                cost = _tour_distance(start_index, end_index, candidate, matrix)
                iterations += 1
                if cost < best_cost - 1e-6:
                    best, best_cost = candidate, cost
                    improved = True
                if iterations >= max_iterations:
                    break
            if iterations >= max_iterations:
                break
    return best


def schedule_from_matrix(start_index, order, matrix, contexts_by_index, start_time="08:00"):
    """Calcola arrivo/partenza/distanza/durata per ciascuna tappa usando le
    distanze e i tempi REALI della matrice Google (non una stima haversine),
    e valida ogni arrivo contro la finestra oraria della tappa
    (ctx['windows']/ctx['window_source']). 'order' e 'contexts_by_index' sono
    indicizzati come nella matrice (indice 0 = partenza)."""
    try:
        current_time = datetime.strptime(start_time, "%H:%M")
    except ValueError:
        current_time = datetime.strptime("08:00", "%H:%M")
    schedule = []
    prev_index = start_index
    for index in order:
        leg = matrix.get((prev_index, index), {})
        duration = leg.get("duration_seconds") or 0
        current_time = current_time + timedelta(seconds=duration)
        arrival = current_time.strftime("%H:%M")
        ctx = contexts_by_index[index]
        status, message = validate_arrival(arrival, ctx["windows"], ctx["window_source"])
        current_time = current_time + timedelta(minutes=ctx["service_minutes"])
        schedule.append({
            "distance_meters": leg.get("distance_meters"), "duration_seconds": leg.get("duration_seconds"),
            "arrival": arrival, "departure": current_time.strftime("%H:%M"),
            "status": status, "message": message,
        })
        prev_index = index
    return schedule


def repair_time_window_violations(start_index, order, matrix, contexts_by_index, start_time="08:00", max_iterations=150):
    """Euristica locale (non un solver VRPTW completo) che prova a ridurre le
    violazioni di finestra oraria (arrivi 'rosso'/'ambra') spostando una
    tappa alla volta in un'altra posizione della sequenza, accettando la
    mossa solo se riduce le violazioni (o, a parita' di violazioni, la
    distanza totale). Se l'ordine di partenza non ha gia' violazioni non
    viene toccato: l'ottimizzazione per tempo/distanza ha sempre la
    precedenza quando le finestre orarie sono gia' rispettate."""
    def score(seq):
        sched = schedule_from_matrix(start_index, seq, matrix, contexts_by_index, start_time)
        violations = sum(2 for s in sched if s["status"] == "rosso") + sum(1 for s in sched if s["status"] == "ambra")
        distance = sum((s["distance_meters"] or 0) for s in sched)
        return violations, distance
    best = list(order)
    best_violations, best_distance = score(best)
    if best_violations == 0:
        return best
    n = len(best)
    iterations = 0
    improved = True
    while improved and n >= 2 and iterations < max_iterations:
        improved = False
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                candidate = best[:]
                stop = candidate.pop(i)
                candidate.insert(j, stop)
                violations, distance = score(candidate)
                iterations += 1
                if violations < best_violations or (violations == best_violations and distance < best_distance - 1e-6):
                    best, best_violations, best_distance = candidate, violations, distance
                    improved = True
                if iterations >= max_iterations:
                    break
            if iterations >= max_iterations:
                break
    return best


def optimize_route_with_schedule(start, destination, contexts, mode="veloce", start_time="08:00", api_key=None):
    """Punto d'ingresso principale per Fase 2: sceglie l'ordine delle tappe E
    calcola l'orario previsto per ciascuna usando distanze/durate REALI
    (Google Routes API), non solo una stima haversine — cosi' le finestre
    orarie influenzano davvero l'ottimizzazione (repair_time_window_violations),
    non solo una colorazione a posteriori. 'contexts' e' la lista di dict
    prodotta da route_plan_stop_context (lat,lng,service_minutes,windows,
    window_source,...), con 'event_id' gia' aggiunto dal chiamante.

    'Piu' veloce' usa l'ordine calcolato da Google (optimizeWaypointOrder,
    TRAFFIC_AWARE): e' l'unico modo per ottenere un ordine davvero ottimizzato
    sul tempo reale di percorrenza. 'Piu' breve' usa nearest-neighbor+2-opt
    sulla distanza reale (TRAFFIC_UNAWARE) della stessa matrice: un criterio
    davvero diverso (distanza fisica, non tempo), non un'etichetta diversa
    sullo stesso risultato. 'destination' serve solo da ancora per orientare
    l'ottimizzazione (non entra nel programma orario, coerente con la stima
    haversine preesistente): per il caso "arrivo = ultimo ritiro" il
    chiamante puo' passare la partenza stessa come ancora e poi leggere
    l'ultima tappa di 'tappe_ordinate' come vero indirizzo di arrivo.

    Non chiama mai la Route Optimization API enterprise (richiederebbe
    autenticazione OAuth/service-account, incoerente con lo stile
    solo-API-key di questo progetto): usa Routes API (computeRoutes +
    computeRouteMatrix) con un raffinamento locale, sufficiente per un solo
    operatore/veicolo al giorno.

    Ritorna (tappe_ordinate, programma_o_None, sorgente) — sorgente in
    'google'|'vicinanza'|'nessuna'; programma e' None quando la sorgente non
    e' 'google' (il chiamante ricade sulla stima haversine esistente)."""
    api_key = api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    geocoded = [c for c in contexts if c.get("lat") is not None and c.get("lng") is not None]
    has_coords = bool(geocoded) and start.get("lat") is not None and destination.get("lat") is not None
    if api_key and has_coords and len(geocoded) == len(contexts):
        try:
            points = [start] + geocoded + [destination]
            n = len(geocoded)
            stop_indices = list(range(1, 1 + n))
            end_index = n + 1
            matrix = compute_route_matrix_google(api_key, points, traffic_aware=(mode == "veloce"))
            if mode == "veloce":
                google_result = compute_route_google(api_key, start, destination, geocoded, traffic_aware=True)
                order = [stop_indices[i] for i in google_result["order"]]
            else:
                greedy = nearest_neighbor_order_matrix(0, stop_indices, matrix)
                order = two_opt_improve(0, end_index, greedy, matrix)
            order = repair_time_window_violations(0, order, matrix, {i: geocoded[i - 1] for i in stop_indices}, start_time)
            schedule = schedule_from_matrix(0, order, matrix, {i: geocoded[i - 1] for i in stop_indices}, start_time)
            ordered = [geocoded[i - 1] for i in order]
            return ordered, schedule, "google"
        except Exception as exc:
            print(f"[ROUTE] Google Routes/Matrix API non disponibile, uso ordine per vicinanza: {type(exc).__name__}: {exc}", flush=True)
    if has_coords:
        return nearest_neighbor_order(start, geocoded), None, "vicinanza"
    return list(contexts), None, "nessuna"


def _normalize_place_text(value):
    """Confronto tollerante di comune/provincia: 'Firenze' == 'FIRENZE' ==
    ' firenze ' == 'Provincia di Firenze'. Nominatim e Google non
    restituiscono mai il nome nello stesso formato ne' fra loro ne' rispetto
    a come l'operatore l'ha digitato."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"^provincia di\s+", "", text.strip(), flags=re.I)
    return re.sub(r"\s+", " ", text).strip().lower()


def _province_matches(result_province, expected_code):
    if not expected_code:
        return True
    expected_name = PROVINCE_NAMES.get(expected_code.strip().upper(), expected_code)
    return _normalize_place_text(result_province) == _normalize_place_text(expected_name)


def _nominatim_geocode_result(row, provider):
    details = row.get("address") or {}
    city = details.get("city") or details.get("town") or details.get("village") or details.get("municipality") or details.get("hamlet") or ""
    province = details.get("county") or details.get("state_district") or ""
    postcode = details.get("postcode") or ""
    osm_class = row.get("class") or ""
    precision = "civico" if details.get("house_number") else ("via" if osm_class == "highway" else "area")
    return {
        "lat": float(row["lat"]), "lng": float(row["lon"]),
        "city": city, "province": province, "postcode": postcode,
        "precision": precision, "provider": provider,
    }


def _google_geocode_result(result):
    loc = result["geometry"]["location"]
    components = result.get("address_components") or []

    def find(*types):
        for comp in components:
            if any(t in (comp.get("types") or []) for t in types):
                return comp.get("long_name", "")
        return ""

    city = find("locality", "administrative_area_level_3", "postal_town")
    province = find("administrative_area_level_2")
    postcode = find("postal_code")
    has_house_number = bool(find("street_number"))
    location_type = (result.get("geometry") or {}).get("location_type", "")
    precision = "civico" if (has_house_number and location_type in ("ROOFTOP", "RANGE_INTERPOLATED")) else (
        "via" if location_type in ("ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER") else "area"
    )
    return {
        "lat": float(loc["lat"]), "lng": float(loc["lng"]),
        "city": city, "province": province, "postcode": postcode,
        "precision": precision, "provider": "google",
    }


def structured_query_components(street=None, cap=None, city=None, province=None):
    """Componenti separati per una ricerca STRUTTURATA su Nominatim (street/
    city/county/postalcode) invece di una singola stringa libera — molto
    meno ambigua, stesso principio gia' usato da api_zip_lookup in app.py
    per il recupero del CAP, qui esteso alla geocodifica vera e propria."""
    components = {}
    street = (street or "").strip()
    if street:
        components["street"] = street
    city = (city or "").strip()
    if city:
        components["city"] = city
    province = (province or "").strip()
    if province:
        components["county"] = PROVINCE_NAMES.get(province.upper(), province)
    cap = (cap or "").strip()
    if cap:
        components["postalcode"] = cap
    return components


def geocode_address_detailed(address, structured=None, api_key=None):
    """Geocodifica un indirizzo restituendo, oltre a lat/lng, il comune e la
    provincia effettivamente individuati dal provider (per poterli
    confrontare con quelli attesi, vedi match_geocode_location) e una stima
    di precisione ("civico" quando il civico e' stato riconosciuto, "via"
    quando solo la strada, "area" quando solo una zona generica). Tenta, in
    ordine: ricerca STRUTTURATA su Nominatim (quando sono note via/comune/
    provincia separati), ricerca libera su Nominatim sulla stringa
    completa, infine Google Geocoding come ultima risorsa (se configurata
    una GOOGLE_MAPS_API_KEY) — sempre filtrata per comune/provincia quando
    noti, per ridurre ulteriormente l'ambiguita'. Non solleva mai eccezioni:
    in caso di fallimento totale ritorna None (nessuna destinazione
    inventata: spetta al chiamante decidere se bloccare la navigazione)."""
    address = (address or "").strip()
    if structured and structured.get("street"):
        try:
            params = dict(structured)
            params.update({"format": "jsonv2", "limit": "1", "addressdetails": "1", "countrycodes": "it"})
            query = urllib.parse.urlencode(params)
            req = urllib.request.Request(
                f"https://nominatim.openstreetmap.org/search?{query}",
                headers={"Accept": "application/json", "User-Agent": "PetParadiseManager/1.0 (route geocoding)"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            if payload:
                return _nominatim_geocode_result(payload[0], provider="nominatim-strutturato")
        except Exception as exc:
            print(f"[ROUTE] Nominatim strutturato non disponibile: {type(exc).__name__}: {exc}", flush=True)
    if address:
        try:
            params = urllib.parse.urlencode({"q": address, "format": "jsonv2", "limit": "1", "addressdetails": "1"})
            req = urllib.request.Request(
                f"https://nominatim.openstreetmap.org/search?{params}",
                headers={"Accept": "application/json", "User-Agent": "PetParadiseManager/1.0 (route geocoding)"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            if payload:
                return _nominatim_geocode_result(payload[0], provider="nominatim")
        except Exception as exc:
            print(f"[ROUTE] Nominatim non disponibile: {type(exc).__name__}: {exc}", flush=True)
    api_key = api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if api_key and address:
        try:
            google_params = {"address": address, "key": api_key, "region": "it"}
            if structured and (structured.get("city") or structured.get("county")):
                components = []
                if structured.get("city"):
                    components.append(f"locality:{structured['city']}")
                if structured.get("county"):
                    components.append(f"administrative_area:{structured['county']}")
                components.append("country:IT")
                google_params["components"] = "|".join(components)
            params = urllib.parse.urlencode(google_params)
            req = urllib.request.Request(f"https://maps.googleapis.com/maps/api/geocode/json?{params}", method="GET")
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            results = payload.get("results") or []
            if results:
                return _google_geocode_result(results[0])
        except Exception as exc:
            print(f"[ROUTE] Google Geocoding non disponibile: {type(exc).__name__}: {exc}", flush=True)
    return None


def match_geocode_location(result, expected_city=None, expected_province=None):
    """Confronta comune/provincia restituiti dal geocoding con quelli attesi
    (gia' noti dall'anagrafica collegata all'evento: pratica/cliente/
    veterinario) e ritorna uno stato esplicito — mai un booleano generico,
    perche' il chiamante deve poter distinguere:
      - "validato": comune (e provincia, se nota) combaciano E il civico e'
        stato riconosciuto dal provider — la coordinata e' affidabile.
      - "approssimato": un risultato esiste e comune/provincia combaciano
        (o non erano noti per il confronto), ma senza conferma del civico —
        o comune/provincia non erano affatto noti per un confronto.
      - "ambiguo": il provider ha restituito un comune/provincia DIVERSO da
        quello atteso (es. Pisa invece di Empoli) — il risultato va
        rifiutato, mai usato come coordinata.
      - "non_trovato": nessun risultato dal geocoding."""
    if not result:
        return "non_trovato"
    city_known = bool((expected_city or "").strip())
    province_known = bool((expected_province or "").strip())
    if not city_known and not province_known:
        # Nessun comune/provincia noto per un confronto reale: il
        # risultato non puo' mai essere promosso a "validato" solo perche'
        # nulla lo contraddice - resta un'approssimazione.
        return "approssimato"
    city_ok = (not city_known) or _normalize_place_text(result.get("city")) == _normalize_place_text(expected_city)
    province_ok = (not province_known) or _province_matches(result.get("province"), expected_province)
    if not (city_ok and province_ok):
        return "ambiguo"
    if result.get("precision") == "civico":
        return "validato"
    return "approssimato"


def format_destination_address(street=None, cap=None, city=None, province=None, extra=None):
    """Costruisce l'indirizzo COMPLETO da inviare a Google Maps (o da
    geocodificare) a partire dai singoli campi gia' presenti nel gestionale.
    Via e civico NON vengono mai separati ne' toccati (arrivano gia' come
    un'unica stringa libera dai form di pratica/cliente/veterinario, es.
    'Via Roma 10/A' — normalizzarli rischierebbe di alterarne il
    significato, richiesta esplicita dell'utente): comune/provincia/CAP
    vengono invece SEMPRE aggiunti quando disponibili, per disambiguare
    (es. 'Via Roma 10, 50053 Empoli, FI, Italia' invece del solo 'Via Roma
    10', che Google Maps potrebbe risolvere in un comune omonimo diverso).
    `extra` e' per un'eventuale frazione/localita' aggiuntiva."""
    street = (street or "").strip()
    extra = (extra or "").strip()
    cap = (cap or "").strip()
    city = (city or "").strip()
    province = (province or "").strip().upper()
    locality = " ".join(x for x in (cap, city) if x)
    parts = [p for p in (street, extra, locality, province) if p]
    if not parts:
        # Nessun componente reale: "Italia" da solo non e' un indirizzo,
        # nessuna destinazione inventata (richiesta esplicita dell'utente).
        return ""
    parts.append("Italia")
    return ", ".join(parts)


def geocode_address(address, api_key=None):
    """Geocodifica un indirizzo, solo lat/lng — wrapper mantenuto per i
    chiamanti gia' esistenti (Percorso giornaliero) che non hanno bisogno di
    validare comune/provincia. Per i nuovi utilizzi (destinazione dei
    pulsanti "Naviga") usa invece geocode_address_detailed, che restituisce
    anche comune/provincia/precisione per poter validare il risultato con
    match_geocode_location, o piu' direttamente resolve_navigation_destination
    qui sotto — nessuna seconda logica di geocodifica indipendente, questa
    funzione e geocode_address_detailed condividono le stesse chiamate
    HTTP."""
    result = geocode_address_detailed(address, api_key=api_key)
    if result:
        return result["lat"], result["lng"]
    return None, None


def search_address_suggestions(query, limit=5):
    """Suggerimenti di indirizzo mentre l'utente digita (equivalente
    "gratuito" di Google Places Autocomplete, stesso servizio Nominatim gia'
    usato da geocode_address e dal recupero CAP in app.py): ogni risultato
    include gia' lat/lng, cosi' selezionandolo si evita del tutto la
    geocodifica pigra al primo utilizzo per un percorso. Non solleva mai
    eccezioni: in caso di errore/timeout ritorna una lista vuota."""
    query = (query or "").strip()
    if len(query) < 3:
        return []
    try:
        params = urllib.parse.urlencode({
            "q": query, "format": "jsonv2", "limit": str(max(1, min(10, limit))),
            "addressdetails": "0", "countrycodes": "it",
        })
        req = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={"Accept": "application/json", "User-Agent": "PetParadiseManager/1.0 (address suggestions)"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return [
            {"display_name": row.get("display_name", ""), "lat": float(row["lat"]), "lng": float(row["lon"])}
            for row in payload if row.get("lat") and row.get("lon")
        ]
    except Exception as exc:
        print(f"[ROUTE] Suggerimenti indirizzo non disponibili: {type(exc).__name__}: {exc}", flush=True)
        return []


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


def resolve_navigation_destination(conn, *, street=None, extra=None, cap=None, city=None, province=None,
                                    fallback_text=None, api_key=None):
    """Punto UNICO per determinare la destinazione da inviare a Google Maps
    per QUALSIASI pulsante "Naviga" del gestionale (ritiri, ritiri presso
    veterinario, riconsegne, percorso giornaliero, o qualunque altro punto
    futuro) — richiesta esplicita dell'utente: stessa logica ovunque,
    nessuna geocodifica parallela. Riusa geocode_address_detailed e
    geocode_cache, non introduce un secondo sistema.

    Non inventa mai una destinazione: se i dati disponibili non bastano a
    costruire nemmeno un indirizzo minimo, ritorna status "non_trovato" con
    maps_destination vuoto — il chiamante deve allora disabilitare il
    pulsante "Naviga", non mandare comunque l'operatore da qualche parte.

    Non usa mai una coordinata NON validata come destinazione: le
    coordinate finiscono nel link a Google Maps solo quando status e'
    "validato" (comune/provincia/civico confermati dal geocoding).
    Altrimenti ("approssimato"/"ambiguo") si invia comunque il testo
    dell'indirizzo COMPLETO che abbiamo costruito noi stessi (via+civico+
    CAP+comune+provincia) — resta un'informazione nostra, corretta,
    indipendente da un geocoding che puo' aver sbagliato comune; e' sempre
    piu' preciso di oggi (solo la via), ma senza fidarsi ciecamente di un
    punto sulla mappa potenzialmente nel posto sbagliato.

    Ritorna un dict:
      status: "validato" | "approssimato" | "ambiguo" | "non_trovato"
      lat, lng: presenti solo quando status=="validato"
      display_address: indirizzo completo, per mostrarlo all'operatore
      maps_destination: valore pronto per "...maps/dir/?api=1&destination="
        (ancora da passare a urllib.parse.quote) — stringa vuota quando
        status=="non_trovato"."""
    street = (street or "").strip()
    display_address = format_destination_address(street=street, cap=cap, city=city, province=province, extra=extra)
    if not display_address:
        fallback_text = (fallback_text or "").strip()
        if not fallback_text:
            return {"status": "non_trovato", "lat": None, "lng": None, "display_address": "", "maps_destination": ""}
        display_address = fallback_text
        street = street or fallback_text
    cache_key = display_address
    cached = conn.execute(
        "SELECT lat,lng,matched_city,matched_province,precision FROM geocode_cache WHERE address=?", (cache_key,)
    ).fetchone()
    if cached and cached["lat"] is not None:
        result = {"city": cached["matched_city"], "province": cached["matched_province"], "precision": cached["precision"]}
        status = match_geocode_location(result, expected_city=city, expected_province=province)
        if status == "validato":
            return {"status": "validato", "lat": cached["lat"], "lng": cached["lng"],
                     "display_address": display_address, "maps_destination": f'{cached["lat"]},{cached["lng"]}'}
        return {"status": status, "lat": None, "lng": None,
                 "display_address": display_address, "maps_destination": display_address}
    structured = structured_query_components(street=street, cap=cap, city=city, province=province) if street else None
    result = geocode_address_detailed(display_address, structured=structured, api_key=api_key)
    status = match_geocode_location(result, expected_city=city, expected_province=province)
    if result:
        stamp = rome_now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO geocode_cache(address,lat,lng,matched_city,matched_province,precision,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(address) DO UPDATE SET "
            "lat=excluded.lat,lng=excluded.lng,matched_city=excluded.matched_city,"
            "matched_province=excluded.matched_province,precision=excluded.precision,updated_at=excluded.updated_at",
            (cache_key, result["lat"], result["lng"], result.get("city", ""), result.get("province", ""),
             result.get("precision", ""), stamp),
        )
    if status == "validato":
        return {"status": "validato", "lat": result["lat"], "lng": result["lng"],
                 "display_address": display_address, "maps_destination": f'{result["lat"]},{result["lng"]}'}
    return {"status": status, "lat": None, "lng": None,
             "display_address": display_address, "maps_destination": display_address}


def fetch_place_hours_google(api_key, place_id):
    """Orari settimanali da Google Places (New) Place Details. Ritorna un
    dict {day_of_week: {'closed':bool,'morning_start':,'morning_end':,
    'afternoon_start':,'afternoon_end':}} per tutti e 7 i giorni (0=lunedi'..
    6=domenica, stessa convenzione di veterinarian_hours/date.weekday()),
    oppure None se la struttura non ha orari pubblicati o la chiamata
    fallisce — mai un'eccezione: il chiamante deve poter continuare a
    funzionare anche senza questi dati (orari manuali o nessun vincolo)."""
    place_id = (place_id or "").strip()
    if not place_id:
        return None
    url = PLACE_DETAILS_API_URL.format(place_id=urllib.parse.quote(place_id))
    req = urllib.request.Request(
        url,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": "regularOpeningHours"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        print(f"[ROUTE] Google Places Details non disponibile: {type(exc).__name__}: {exc}", flush=True)
        return None
    periods = (payload.get("regularOpeningHours") or {}).get("periods")
    if not periods:
        return None
    by_day = {i: [] for i in range(7)}
    for period in periods:
        open_p, close_p = period.get("open"), period.get("close")
        if not open_p or not close_p or open_p.get("day") != close_p.get("day"):
            continue  # orario che attraversa la mezzanotte: caso raro per un veterinario, ignorato in modo sicuro
        day = (int(open_p["day"]) + 6) % 7  # Google: 0=domenica -> nostra convenzione 0=lunedi'
        start = f"{int(open_p.get('hour', 0)):02d}:{int(open_p.get('minute', 0)):02d}"
        end = f"{int(close_p.get('hour', 0)):02d}:{int(close_p.get('minute', 0)):02d}"
        by_day[day].append((start, end))
    hours = {}
    for day, windows in by_day.items():
        windows.sort()
        if not windows:
            hours[day] = {"closed": True, "morning_start": None, "morning_end": None, "afternoon_start": None, "afternoon_end": None}
            continue
        morning = windows[0]
        afternoon = windows[1] if len(windows) > 1 else (None, None)
        hours[day] = {"closed": False, "morning_start": morning[0], "morning_end": morning[1],
                      "afternoon_start": afternoon[0], "afternoon_end": afternoon[1]}
    return hours


def ensure_vet_hours_from_google(conn, vet_row, api_key=None, force=False):
    """Recupera e salva gli orari di un veterinario da Google Places, con
    cache e priorita' assoluta agli orari manuali: non li sovrascrive MAI
    automaticamente (hours_source=='manuale'), a meno che 'force' non sia
    esplicitamente richiesto da un'azione utente diretta ("Aggiorna da
    Google adesso"). Senza 'force', aggiorna solo se non e' mai stato
    recuperato prima o se il recupero precedente e' scaduto (TTL
    configurabile in settings, chiave route_google_hours_ttl_days). Non
    solleva mai eccezioni e non blocca mai la creazione del percorso: in
    caso di errore o assenza di orari lascia lo stato invariato. Ritorna
    True se ha scritto nuovi orari, False altrimenti."""
    place_id = vet_row["google_place_id"] if "google_place_id" in vet_row.keys() else None
    if not place_id:
        return False
    if not force and vet_row["hours_source"] == "manuale":
        return False
    if not force:
        updated_at = vet_row["hours_updated_at"]
        if updated_at:
            ttl_row = conn.execute("SELECT value FROM settings WHERE key='route_google_hours_ttl_days'").fetchone()
            try:
                ttl_days = int(ttl_row["value"]) if ttl_row else DEFAULT_GOOGLE_HOURS_TTL_DAYS
            except (TypeError, ValueError):
                ttl_days = DEFAULT_GOOGLE_HOURS_TTL_DAYS
            try:
                age_days = (rome_now() - datetime.fromisoformat(updated_at)).days
                if age_days < ttl_days:
                    return False
            except ValueError:
                pass
    api_key = api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        hours = fetch_place_hours_google(api_key, place_id)
    except Exception as exc:
        print(f"[ROUTE] Recupero orari Google fallito per il veterinario {vet_row['id']}: {type(exc).__name__}: {exc}", flush=True)
        hours = None
    stamp = rome_now().isoformat(timespec="seconds")
    if hours is None:
        # Nessun orario disponibile (place ID senza orari pubblicati, API
        # irraggiungibile, quota esaurita): segnamo comunque il tentativo per
        # non richiamare l'API ad ogni singolo calcolo del percorso, ma senza
        # toccare eventuali orari gia' presenti.
        conn.execute("UPDATE veterinarians SET hours_source='assente', hours_updated_at=? WHERE id=?", (stamp, vet_row["id"]))
        return False
    for day, day_hours in hours.items():
        conn.execute(
            """INSERT INTO veterinarian_hours(veterinarian_id,day_of_week,closed,morning_start,morning_end,afternoon_start,afternoon_end)
              VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(veterinarian_id,day_of_week) DO UPDATE SET closed=excluded.closed,morning_start=excluded.morning_start,
                morning_end=excluded.morning_end,afternoon_start=excluded.afternoon_start,afternoon_end=excluded.afternoon_end""",
            (vet_row["id"], day, 1 if day_hours["closed"] else 0, day_hours["morning_start"], day_hours["morning_end"],
             day_hours["afternoon_start"], day_hours["afternoon_end"]),
        )
    conn.execute("UPDATE veterinarians SET hours_source='google', hours_updated_at=? WHERE id=?", (stamp, vet_row["id"]))
    return True


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
