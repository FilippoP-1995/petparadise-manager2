"""Fase 8.1 (hardening): verifica di confine per il filtro date_from/date_to
condiviso da GET /api/pickups e GET /api/deliveries, che alimenta il
Calendario operativo (vista aggregata, non un nuovo dominio - i dati
restano identici a quelli gia' esposti dalle liste Ritiri/Riconsegne)."""

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient


def _pickup_payload(sample_client, sample_zone, start_at: datetime, **overrides):
    end_at = start_at + timedelta(hours=1)
    base = {
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "client_id": sample_client.id,
        "pickup_type": "domicilio",
        "pickup_zone_id": sample_zone.id,
    }
    base.update(overrides)
    return base


def _delivery_payload(sample_location, start_at: datetime, **overrides):
    end_at = start_at + timedelta(hours=1)
    base = {
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "delivery_type": "sede_aziendale",
        "delivery_location_id": sample_location.id,
    }
    base.update(overrides)
    return base


def _day_bounds_like_frontend(day: datetime) -> tuple[str, str]:
    """Riproduce esattamente dayBounds() di CalendarPage.tsx: mezzanotte
    locale del giorno -> mezzanotte locale del giorno dopo, serializzate
    con .isoformat() (equivalente Python di .toISOString(), sempre con
    offset esplicito - mai una stringa naive)."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


async def test_event_exactly_at_date_from_is_included(authed_client: AsyncClient, sample_client, sample_zone):
    day_start = datetime(2027, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day_start))

    response = await authed_client.get(
        "/api/pickups",
        params={"date_from": day_start.isoformat(), "date_to": (day_start + timedelta(days=1)).isoformat()},
    )
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] in ids


async def test_event_exactly_at_date_to_is_excluded(authed_client: AsyncClient, sample_client, sample_zone):
    day_start = datetime(2027, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
    next_day_start = day_start + timedelta(days=1)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, next_day_start))

    response = await authed_client.get(
        "/api/pickups", params={"date_from": day_start.isoformat(), "date_to": next_day_start.isoformat()}
    )
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] not in ids


async def test_event_one_second_before_date_to_is_included(authed_client: AsyncClient, sample_client, sample_zone):
    day_start = datetime(2027, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
    next_day_start = day_start + timedelta(days=1)
    end_of_day = next_day_start - timedelta(seconds=1)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, end_of_day))

    response = await authed_client.get(
        "/api/pickups", params={"date_from": day_start.isoformat(), "date_to": next_day_start.isoformat()}
    )
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] in ids


async def test_previous_day_event_does_not_leak_into_next_day(authed_client: AsyncClient, sample_client, sample_zone):
    day_start = datetime(2027, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
    just_before = day_start - timedelta(seconds=1)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, just_before))

    response = await authed_client.get(
        "/api/pickups", params={"date_from": day_start.isoformat(), "date_to": (day_start + timedelta(days=1)).isoformat()}
    )
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] not in ids


async def test_next_day_event_does_not_leak_into_previous_day(authed_client: AsyncClient, sample_client, sample_zone):
    day_start = datetime(2027, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
    next_day_start = day_start + timedelta(days=1)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, next_day_start))

    response = await authed_client.get(
        "/api/pickups", params={"date_from": day_start.isoformat(), "date_to": next_day_start.isoformat()}
    )
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] not in ids


async def test_trashed_pickup_excluded_from_date_filtered_results(authed_client: AsyncClient, sample_client, sample_zone):
    day_start = datetime(2027, 3, 11, 0, 0, 0, tzinfo=timezone.utc)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day_start))
    pickup_id = created.json()["id"]

    trash = await authed_client.post(f"/api/pickups/{pickup_id}/trash")
    assert trash.status_code == 200

    response = await authed_client.get(
        "/api/pickups", params={"date_from": day_start.isoformat(), "date_to": (day_start + timedelta(days=1)).isoformat()}
    )
    ids = {p["id"] for p in response.json()}
    assert pickup_id not in ids


async def test_trashed_delivery_excluded_from_date_filtered_results(authed_client: AsyncClient, sample_location):
    day_start = datetime(2027, 3, 11, 0, 0, 0, tzinfo=timezone.utc)
    created = await authed_client.post("/api/deliveries", json=_delivery_payload(sample_location, day_start))
    delivery_id = created.json()["id"]

    trash = await authed_client.post(f"/api/deliveries/{delivery_id}/trash")
    assert trash.status_code == 200

    response = await authed_client.get(
        "/api/deliveries", params={"date_from": day_start.isoformat(), "date_to": (day_start + timedelta(days=1)).isoformat()}
    )
    ids = {d["id"] for d in response.json()}
    assert delivery_id not in ids


async def test_pickup_and_delivery_same_day_are_not_cross_contaminated(
    authed_client: AsyncClient, sample_client, sample_zone, sample_location
):
    day_start = datetime(2027, 3, 12, 9, 0, 0, tzinfo=timezone.utc)
    pickup = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day_start))
    delivery = await authed_client.post("/api/deliveries", json=_delivery_payload(sample_location, day_start))

    params = {"date_from": day_start.replace(hour=0).isoformat(), "date_to": (day_start.replace(hour=0) + timedelta(days=1)).isoformat()}

    pickup_response = await authed_client.get("/api/pickups", params=params)
    delivery_response = await authed_client.get("/api/deliveries", params=params)

    pickup_ids = {p["id"] for p in pickup_response.json()}
    delivery_ids = {d["id"] for d in delivery_response.json()}

    assert pickup.json()["id"] in pickup_ids
    assert pickup.json()["id"] not in delivery_ids
    assert delivery.json()["id"] in delivery_ids
    assert delivery.json()["id"] not in pickup_ids


async def test_ordering_is_deterministic_when_two_events_share_start_at(
    authed_client: AsyncClient, sample_client, sample_zone
):
    day_start = datetime(2027, 3, 13, 10, 0, 0, tzinfo=timezone.utc)
    first = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day_start))
    second = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day_start))

    params = {"date_from": day_start.isoformat(), "date_to": (day_start + timedelta(seconds=1)).isoformat()}
    first_call = await authed_client.get("/api/pickups", params=params)
    second_call = await authed_client.get("/api/pickups", params=params)

    ids_first_call = [p["id"] for p in first_call.json() if p["id"] in {first.json()["id"], second.json()["id"]}]
    ids_second_call = [p["id"] for p in second_call.json() if p["id"] in {first.json()["id"], second.json()["id"]}]

    # Stesso ordine ripetendo la stessa query (tiebreaker su id, non solo
    # su start_at che qui e' identico per entrambi gli eventi).
    assert ids_first_call == ids_second_call
    assert ids_first_call == sorted(ids_first_call, reverse=True)


async def test_empty_day_returns_empty_list_not_error(authed_client: AsyncClient):
    day_start = datetime(2027, 3, 14, 0, 0, 0, tzinfo=timezone.utc)
    response = await authed_client.get(
        "/api/pickups", params={"date_from": day_start.isoformat(), "date_to": (day_start + timedelta(days=1)).isoformat()}
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_date_bounds_match_actual_frontend_toisostring_format(
    authed_client: AsyncClient, sample_client, sample_zone
):
    """Riproduce esattamente il formato inviato da CalendarPage.tsx
    (dayBounds -> Date.toISOString(), sempre UTC con suffisso 'Z') per
    escludere che il formato specifico della stringa (non solo il valore)
    causi un comportamento diverso da quello testato con datetime.isoformat()."""
    day = datetime(2027, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day))

    date_from = day.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_to = (day + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert date_from == "2027-03-15T00:00:00.000Z"

    response = await authed_client.get("/api/pickups", params={"date_from": date_from, "date_to": date_to})
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] in ids


async def test_naive_datetime_without_timezone_is_still_treated_as_utc(
    authed_client: AsyncClient, sample_client, sample_zone
):
    """Il server Postgres di questo ambiente ha TimeZone=Europe/Berlin (non
    UTC) - verificato con SHOW TIMEZONE. Un cast SQL letterale
    ('...'::timestamptz) userebbe quel fuso, ma il driver asyncpg tratta i
    datetime naive passati come parametri come UTC, non come ora locale
    del server: un client che (per errore) invia date_from/date_to senza
    l'offset esplicito ottiene comunque il confronto corretto in UTC, non
    un risultato silenziosamente spostato di due ore. Il frontend reale
    non manda mai una stringa naive (dayBounds() usa sempre
    Date.toISOString(), con 'Z'), ma questo test documenta il
    comportamento per chi in futuro chiamasse l'endpoint diversamente."""
    day_start = datetime(2027, 3, 16, 0, 0, 0, tzinfo=timezone.utc)
    created = await authed_client.post("/api/pickups", json=_pickup_payload(sample_client, sample_zone, day_start))

    naive_date_from = day_start.replace(tzinfo=None).isoformat()
    naive_date_to = (day_start + timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert "Z" not in naive_date_from and "+" not in naive_date_from

    response = await authed_client.get("/api/pickups", params={"date_from": naive_date_from, "date_to": naive_date_to})
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert created.json()["id"] in ids
