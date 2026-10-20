"""Dati finti realistici per l'ambiente di SVILUPPO (mai per la produzione).

Riempie un database NUOVO con clienti, pratiche in tutti gli stati (con
acconti e saldi), cicli del programma cremazioni e alcune cliniche
veterinarie del portale Pet Paradise Partners con le loro richieste.
Tutti i nomi, telefoni, codici fiscali, email e indirizzi sono inventati:
nessun dato reale.

Le pratiche, i pagamenti, i cambi di stato e le richieste del portale
passano dalle stesse funzioni usate dall'app (create_practice,
apply_practice_quick_state, partner_service.create_request, ...), cosi' i
dati derivati (numeri pratica, clienti, storico, stato pagamento, eventi
calendario) sono coerenti come se fossero stati inseriti a mano.

Uso (la cartella deve essere nuova o vuota):

    PPM_DATA_DIR=/percorso/scratch/devdata python seed_dev_data.py
    PPM_DATA_DIR=/percorso/scratch/devdata PPM_PORT=8090 python app.py

Accessi creati (solo sviluppo):
    gestionale: admin / petparadise (utente iniziale dell'app)
    portale /partner: vedi PARTNER_PASSWORD e gli utenti stampati a fine esecuzione
"""

import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PARTNER_PASSWORD = "prova-partner"
SEED = 20261020


def _guard_data_dir():
    """Rifiuta qualsiasi destinazione che possa essere la produzione."""
    raw = os.environ.get("PPM_DATA_DIR", "").strip()
    if not raw:
        sys.exit("ERRORE: imposta PPM_DATA_DIR su una cartella di prova (es. una cartella nuova in /tmp).")
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        sys.exit("ERRORE: rilevato un servizio Render. I dati finti non si caricano mai sui server.")
    target = Path(raw).resolve()
    if target == Path("/var/data") or Path("/var/data") in target.parents:
        sys.exit("ERRORE: /var/data e' il disco di produzione. Usa una cartella di prova.")
    db_file = target / "pet_paradise.db"
    if db_file.exists():
        import sqlite3
        conn = sqlite3.connect(db_file)
        try:
            has_practices = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='practices'").fetchone()
            count = conn.execute("SELECT COUNT(*) FROM practices").fetchone()[0] if has_practices else 0
        finally:
            conn.close()
        if count:
            sys.exit(f"ERRORE: {db_file} contiene gia' {count} pratiche. "
                     "Lo script lavora solo su un database nuovo: usa una cartella vuota.")
    return target


DATA_DIR = _guard_data_dir()

import app  # noqa: E402  (dopo il controllo: app legge PPM_DATA_DIR all'import)
import partner_service as ps  # noqa: E402

rng = random.Random(SEED)

FIRST_NAMES = ["Giulia", "Marco", "Chiara", "Luca", "Sara", "Andrea", "Elena", "Matteo", "Francesca", "Davide",
               "Martina", "Simone", "Valentina", "Lorenzo", "Silvia", "Paolo", "Alessia", "Riccardo", "Laura", "Stefano"]
LAST_NAMES = ["Bianchi", "Ferretti", "Galli", "Lombardi", "Moretti", "Barbieri", "Fontana", "Santoro", "Mariani",
              "Rinaldi", "Caruso", "Ferrara", "Testa", "Pellegrini", "Marchetti", "Grassi", "Benedetti", "Parenti"]
TOWNS = [("Livorno", "LI", "57121", "L"), ("Livorno", "LI", "57124", "L"), ("Collesalvetti", "LI", "57014", "L"),
         ("Rosignano Marittimo", "LI", "57016", "L"), ("Empoli", "FI", "50053", "E"), ("Vinci", "FI", "50059", "E"),
         ("Fucecchio", "FI", "50054", "E"), ("Pisa", "PI", "56121", "P"), ("Viareggio", "LU", "55049", "V")]
STREETS = ["Via dei Glicini", "Via delle Ortensie", "Viale dei Tigli", "Via del Mulino Vecchio", "Via delle Lucciole",
           "Via dei Gabbiani", "Via della Pineta", "Piazza dei Mughetti", "Via del Sole Basso", "Via delle Querce"]
DOGS = [("Cane", "Meticcio"), ("Cane", "Labrador"), ("Cane", "Jack Russell"), ("Cane", "Pastore tedesco"),
        ("Cane", "Bassotto"), ("Cane", "Golden Retriever"), ("Cane", "Barboncino")]
CATS = [("Gatto", "Europeo"), ("Gatto", "Persiano"), ("Gatto", "Certosino"), ("Gatto", "Siamese")]
OTHERS = [("Coniglio", "Ariete"), ("Furetto", ""), ("Criceto", "")]
PET_NAMES = ["Fido", "Luna", "Milo", "Kira", "Briciola", "Rocky", "Nala", "Pepe", "Zoe", "Oliver", "Maya", "Leo",
             "Birba", "Lucky", "Molly", "Tobia", "Nuvola", "Argo", "Stella", "Otto", "Mia", "Bruno", "Lilla", "Ugo"]

# Cliniche INVENTATE (le anagrafiche precaricate dall'app sono strutture reali:
# qui non vengono usate per il portale ne' per le pratiche finte).
FAKE_CLINICS = [
    {"short_name": "IL FARO (PROVA)", "clinic_name": "Clinica Veterinaria Il Faro (dati di prova)",
     "doctor_name": "Dr.ssa Anna Prova", "phone": "0586 000101", "address": "Via del Faro Finto, 1, 57121 Livorno LI",
     "city": "Livorno", "username": "prova.faro", "has_freezer": True, "vouchers": True},
    {"short_name": "SAN GIORGIO (PROVA)", "clinic_name": "Ambulatorio Veterinario San Giorgio (dati di prova)",
     "doctor_name": "Dr. Marco Esempio", "phone": "0571 000202", "address": "Via dei Campi Finti, 22, 50053 Empoli FI",
     "city": "Empoli", "username": "prova.sangiorgio", "has_freezer": False, "vouchers": False},
    {"short_name": "LE DUNE (PROVA)", "clinic_name": "Centro Veterinario Le Dune (dati di prova)",
     "doctor_name": "Dr.ssa Sara Demo", "phone": "0586 000303", "address": "Viale delle Dune Finte, 8, 57016 Rosignano Marittimo LI",
     "city": "Rosignano Marittimo", "username": "prova.dune", "has_freezer": True, "vouchers": False},
]


class SeedError(RuntimeError):
    pass


class Handler(app.App):
    """Gestore della richiesta senza socket: gli handler dell'app ricevono il
    form da qui e le risposte (redirect/pagine/JSON) vengono solo registrate."""

    def __init__(self):  # noqa: D401 - nessuna connessione HTTP
        self.path = "/"
        self.headers = {}
        self._form = {}
        self.last_redirect = None
        self.last_json = None

    def form(self):
        return dict(self._form)

    def redirect(self, path):
        self.last_redirect = path

    def send_json(self, obj, status=200):
        self.last_json = (obj, status)
        if status >= 400:
            raise SeedError(f"risposta {status}: {obj}")

    def send_html(self, content, status=200):
        raise SeedError("l'app ha mostrato una pagina invece di salvare (dati non validi?)")

    def new_page(self, user, draft=None, error="", **kwargs):
        raise SeedError(f"pratica non creata: {error}")

    def duplicate_client_page(self, user, draft, duplicates):
        raise SeedError("pratica non creata: possibile cliente duplicato")

    def send_error(self, code, message=None):
        raise SeedError(f"errore {code}: {message or ''}")

    def calendar_event_detail(self, user, event_id, error=""):
        raise SeedError(f"evento {event_id}: {error}")


class BackdatedClock:
    """Fa risultare create in una data passata le pratiche (e il loro storico),
    cosi' Archivio e Bilanci mostrano piu' mesi. Cambia solo app.now()."""

    def __init__(self):
        self.original = app.now

    def at(self, moment):
        app.now = lambda: moment.isoformat(timespec="seconds")

    def reset(self):
        app.now = self.original


def fake_tax_code(first, last, birth_year):
    letters = lambda s: (("".join(ch for ch in s.upper() if ch.isalpha()) + "XXX")[:3])
    months = "ABCDEHLMPRST"
    return (f"{letters(last)}{letters(first)}{birth_year % 100:02d}{rng.choice(months)}"
            f"{rng.randint(1, 28):02d}Z{rng.randint(100, 999)}{rng.choice('ABCDEFGHJKLMNPRSTVWXYZ')}")


def fake_phone():
    return f"3{rng.randint(20, 99)} 555 {rng.randint(1000, 9999)}"


def fake_owner():
    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    town, prov, zip_code, provenance = rng.choice(TOWNS)
    return {
        "owner_first_name": first, "owner_last_name": last, "owner_phone": fake_phone(),
        "owner_email": f"{first}.{last}.{rng.randint(10, 99)}@example.com".lower(),
        "owner_tax_code": fake_tax_code(first, last, rng.randint(1950, 2000)),
        "owner_street": f"{rng.choice(STREETS)}, {rng.randint(1, 120)}",
        "owner_city": town, "owner_province": prov, "owner_zip": zip_code, "provenance": provenance,
        "destination_branch": "Empoli" if provenance == "E" else "Livorno",
    }


def fake_animal(service):
    if service == "Cremazione collettiva":
        species, breed = rng.choice(CATS + OTHERS + DOGS[:2])
    else:
        species, breed = rng.choice(DOGS + DOGS + CATS + OTHERS[:1])
    weight = {"Cane": rng.randint(4, 38), "Gatto": rng.randint(3, 7)}.get(species, 1)
    return {"animal_name": rng.choice(PET_NAMES), "species": species, "breed": breed,
            "estimated_weight": str(weight), "age_years": str(rng.randint(2, 17))}


def prices(service, with_urn):
    if service == "Cremazione collettiva":
        return {"price_cremation": str(rng.choice([60, 80, 100])), "price_pickup": str(rng.choice([0, 30]))}
    out = {"price_cremation": str(rng.choice([180, 220, 260, 320])), "price_pickup": str(rng.choice([30, 40, 50]))}
    if with_urn:
        out["price_urn"] = str(rng.choice([45, 70, 90]))
    return out


def payment_fields(kind, total, day):
    """kind: nessuno / acconto / pagato. Usa i campi Acconto/Rimanenza del form."""
    method = rng.choice(["Pos", "Contanti", "Bonifico"])
    if kind == "nessuno" or total <= 0:
        return {}
    if kind == "acconto":
        return {"acconto_w_totale": f"{min(100, total / 2):.2f}", "acconto_w_data": day,
                "acconto_w_modalita": method}
    acconto = min(100, total / 2)
    return {"acconto_w_totale": f"{acconto:.2f}", "acconto_w_data": day, "acconto_w_modalita": method,
            "saldo_w_totale": f"{total - acconto:.2f}", "saldo_w_data": day, "saldo_w_modalita": method,
            "saldo_w_totale_touched": "1"}


def create_practice(h, admin, form, created_at=None, clock=None):
    h._form = form
    if clock and created_at:
        clock.at(created_at)
    try:
        h.create_practice(admin)
    finally:
        if clock:
            clock.reset()
    if not (h.last_redirect or "").startswith("/pratiche/"):
        raise SeedError(f"pratica non creata (redirect {h.last_redirect})")
    return int(h.last_redirect.rsplit("/", 1)[1])


def set_state(h, admin, pid, *states):
    for state in states:
        with app.db() as c:
            ok, error, _ = h.apply_practice_quick_state(c, pid, state, admin["id"])
        if not ok:
            raise SeedError(f"pratica {pid}: {error}")


def set_event_status(h, admin, event_id, status):
    h._form = {"status": status}
    h.calendar_event_action(admin, event_id, "stato")


def base_form(service, origin, owner, animal, extra_prices, payment):
    form = {"operator_name": "FILIPPO", "service_type": service, "request_origin": origin,
            "transport_method": "Fiat Fiorino", "notes": "Dati di prova generati da seed_dev_data.py"}
    form.update(owner)
    form.update(animal)
    form.update(extra_prices)
    form.update(payment)
    return form


def seed_practices(h, admin, clock):
    """Circa 40 pratiche negli ultimi 5 mesi, in tutti gli stati."""
    today = app.rome_now().date()
    # (stato finale, servizio, pagamento, giorni fa)
    plan = []
    for days_ago in range(150, 20, -6):
        plan.append(("Consegnato", rng.choice(["Cremazione singola"] * 3 + ["Cremazione collettiva"]), "pagato", days_ago))
    for days_ago in (40, 33, 27, 22):
        plan.append(("Smaltito", "Cremazione collettiva", rng.choice(["pagato", "nessuno"]), days_ago))
    for days_ago in (12, 9, 7):
        plan.append(("Da consegnare", "Cremazione singola", rng.choice(["acconto", "pagato"]), days_ago))
    for days_ago in (6, 5):
        plan.append(("Cremato", "Cremazione singola", "acconto", days_ago))
    for days_ago in (4, 3, 2):
        plan.append(("In programma", "Cremazione singola", rng.choice(["acconto", "nessuno"]), days_ago))
    for days_ago in (2, 1, 1, 0):
        plan.append(("Ritirato", rng.choice(["Cremazione singola", "Cremazione collettiva"]), "nessuno", days_ago))
    for days_ago in (0, 0):
        plan.append(("Da ritirare", "Cremazione singola", "nessuno", days_ago))

    created = {}
    in_program = []
    for state, service, payment_kind, days_ago in plan:
        moment = datetime.combine(today - timedelta(days=days_ago), datetime.min.time()).replace(
            hour=rng.randint(8, 17), minute=rng.choice([0, 15, 30, 45]))
        owner = fake_owner()
        animal = fake_animal(service)
        extra = prices(service, with_urn=service == "Cremazione singola" and rng.random() < 0.6)
        total = sum(float(v) for v in extra.values())
        form = base_form(service, rng.choice(["Privato", "Privato", "Consegna in sede"]), owner, animal, extra,
                         payment_fields(payment_kind, total, moment.date().isoformat()))
        form["pickup_date"] = moment.date().isoformat()
        if state == "Da ritirare":
            form["status"] = "Da ritirare"
        pid = create_practice(h, admin, form, created_at=moment, clock=clock)
        path = {
            "Ritirato": [], "Da ritirare": [], "In programma": [],
            "Cremato": ["In programma", "Cremato"],
            "Da consegnare": ["In programma", "Cremato", "Da consegnare"],
            "Consegnato": ["In programma", "Cremato", "Da consegnare", "Consegnato"],
            "Smaltito": ["Smaltito"],
        }[state]
        if state == "Consegnato" and service == "Cremazione collettiva":
            path = ["Cremato", "Consegnato"]
        set_state(h, admin, pid, *path)
        if state == "In programma":
            in_program.append(pid)
        created.setdefault(state, []).append(pid)
    return created, in_program


def seed_cycles(h, admin, practice_ids):
    """Cicli di oggi e domani nel Programma Cremazioni, con gli animali 'In programma'."""
    today = app.rome_now().date()
    for index, pid in enumerate(practice_ids):
        h._form = {"data": (today + timedelta(days=index // 2)).isoformat(), "practice_id": str(pid)}
        h.cremation_create_cycle(admin)
        set_state(h, admin, pid, "In programma")
    h._form = {"data": (today + timedelta(days=2)).isoformat()}
    h.cremation_create_cycle(admin)  # ciclo pianificato ancora vuoto


def seed_partner(h, admin):
    """Cliniche del portale con richieste in ogni stato visibile alla clinica."""
    today = app.rome_now().date()
    users = []
    clinics = []
    with app.db() as c:
        for spec in FAKE_CLINICS:
            stamp = app.now()
            vet_id = c.execute(
                """INSERT INTO veterinarians(clinic_name,short_name,doctor_name,phone,address,city,notes,active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,1,?,?)""",
                (spec["clinic_name"], spec["short_name"], spec["doctor_name"], spec["phone"], spec["address"],
                 spec["city"], "Clinica inventata per l'ambiente di sviluppo", stamp, stamp)).lastrowid
            clinic_id = ps.create_clinic(c, veterinarian_id=vet_id, has_freezer=spec["has_freezer"],
                                         vouchers_enabled=spec["vouchers"],
                                         notify_email=f"{spec['username']}@example.com")
            user_id = ps.add_partner_user(c, clinic_id=clinic_id, email=f"{spec['username']}@example.com",
                                          display_name=spec["doctor_name"], role="titolare")
            c.execute("UPDATE partner_users SET username=?,password_hash=? WHERE id=?",
                      (spec["username"], app.password_hash(PARTNER_PASSWORD), user_id))
            if spec["vouchers"]:
                for _ in range(2):
                    c.execute("INSERT INTO veterinarian_vouchers(veterinarian_id,status,created_at,note) VALUES(?,?,?,?)",
                              (vet_id, "Maturato", stamp, "Buono di prova"))
            clinics.append({"id": clinic_id, "user_id": user_id, "vet_id": vet_id, **spec})
            users.append(spec["username"])

    counter = iter(range(1, 1000))

    def request(clinic, **kwargs):
        owner = fake_owner()
        species, _ = rng.choice(DOGS + CATS)
        args = {"mode": "ritiro_clinica", "service_type": "Cremazione singola", "species": species,
                "weight": f"{rng.randint(3, 30)} kg", "owner_first_name": owner["owner_first_name"],
                "owner_last_name": owner["owner_last_name"], "owner_phone": owner["owner_phone"],
                "animal_name": rng.choice(PET_NAMES), "proposed_date": (today + timedelta(days=1)).isoformat(),
                "proposed_from": "09:00", "proposed_to": "12:00"}
        args.update(kwargs)
        with app.db() as c:
            row, _ = ps.create_request(c, clinic_id=clinic["id"], user_id=clinic["user_id"],
                                       client_request_id=f"seed-{next(counter)}", db_path=app.DB_PATH, **args)
        return dict(row), owner

    def practice_from(req, owner, clinic, payment_kind="nessuno"):
        service = req["service_type"] if req["service_type"] != "Cremazione da decidere" else "Cremazione singola"
        animal = {"animal_name": req["animal_name"], "species": req["species"],
                  "estimated_weight": req["weight_text"].split()[0]}
        extra = prices(service, with_urn=False)
        total = sum(float(v) for v in extra.values())
        owner = {**owner, "owner_first_name": req["owner_first_name"], "owner_last_name": req["owner_last_name"],
                 "owner_phone": req["owner_phone"]}
        form = base_form(service, "Veterinario", owner, animal, extra,
                         payment_fields(payment_kind, total, today.isoformat()))
        form.update({"calendar_event_id": str(req["calendar_event_id"]), "veterinarian_id": str(clinic["vet_id"]),
                     "origin_veterinarian_id": str(clinic["vet_id"]), "origin_mode": "Veterinario",
                     "pickup_date": today.isoformat()})
        return create_practice(h, admin, form)

    faro, sangiorgio, dune = clinics
    # Richiesta ricevuta (evento "Da confermare"), anche urgente e a domicilio.
    request(faro)
    request(sangiorgio, urgent=True, proposed_from="", proposed_to="", proposed_date="")
    request(dune, mode="ritiro_domicilio", pickup_address="Via delle Conchiglie Finte, 4, Rosignano Marittimo",
            proposed_date=(today + timedelta(days=2)).isoformat(), proposed_from="14:00", proposed_to="18:00")
    request(sangiorgio, mode="invio_in_sede", destination_site="Empoli", service_type="Cremazione da decidere")
    # Ritiro programmato (lo staff ha confermato: evento "Da ritirare").
    req, _ = request(faro, proposed_date=(today + timedelta(days=1)).isoformat())
    set_event_status(h, admin, req["calendar_event_id"], "Da ritirare")
    # Ritirato, pratica non ancora creata.
    req, _ = request(dune, proposed_date=today.isoformat(), proposed_from="08:00", proposed_to="23:00")
    set_event_status(h, admin, req["calendar_event_id"], "Da ritirare")
    set_event_status(h, admin, req["calendar_event_id"], "Ritirato")
    # In lavorazione / pronto per la riconsegna / completata: pratica collegata.
    for clinic, states, payment_kind in ((faro, [], "nessuno"), (sangiorgio, ["In programma", "Cremato"], "acconto"),
                                         (faro, ["In programma", "Cremato", "Da consegnare"], "pagato"),
                                         (dune, ["In programma", "Cremato", "Da consegnare", "Consegnato"], "pagato")):
        req, owner = request(clinic, proposed_date=today.isoformat(), proposed_from="08:00", proposed_to="23:00")
        set_event_status(h, admin, req["calendar_event_id"], "Da ritirare")
        set_event_status(h, admin, req["calendar_event_id"], "Ritirato")
        pid = practice_from(req, owner, clinic, payment_kind)
        set_state(h, admin, pid, *states)
    # Annullata dalla clinica.
    req, _ = request(sangiorgio, proposed_date=(today + timedelta(days=3)).isoformat())
    with app.db() as c:
        ps.cancel_request(c, clinic_id=sangiorgio["id"], request_id=req["id"], user_id=sangiorgio["user_id"],
                          db_path=app.DB_PATH)
    # Collettive in congelatore (nessuna fretta), una con buono.
    request(faro, service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="",
            use_voucher=True)
    request(dune, service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
    return users


def main():
    app.init_db()
    h = Handler()
    clock = BackdatedClock()
    with app.db() as c:
        admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
    _, in_program = seed_practices(h, admin, clock)
    seed_cycles(h, admin, in_program)
    partner_users = seed_partner(h, admin)
    with app.db() as c:
        # "Consegnato" mette in coda il WhatsApp di ringraziamento: i numeri sono
        # inventati, quindi nessun messaggio deve mai partire (neanche se un giorno
        # in sviluppo venissero configurate le chiavi WhatsApp).
        for row in c.execute("SELECT DISTINCT practice_id, message_type FROM whatsapp_messages "
                             "WHERE status IN ('programmato','in_invio')").fetchall():
            h.cancel_whatsapp_scheduled(c, row["practice_id"], admin["id"], "Dati di prova: nessun invio",
                                        message_type=row["message_type"])
        pending_whatsapp = c.execute("SELECT COUNT(*) FROM whatsapp_messages "
                                     "WHERE status IN ('programmato','in_invio')").fetchone()[0]
        if pending_whatsapp:
            raise SeedError(f"{pending_whatsapp} messaggi WhatsApp ancora in coda")
    with app.db() as c:
        practices = c.execute("SELECT status, COUNT(*) n FROM practices GROUP BY status ORDER BY n DESC").fetchall()
        payments = c.execute("SELECT payment_status, COUNT(*) n FROM practices GROUP BY payment_status").fetchall()
        clients = c.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        requests = c.execute("SELECT public_status, COUNT(*) n FROM partner_request_status GROUP BY public_status").fetchall()
    print(f"Database di prova: {app.DB_PATH}")
    print(f"Clienti: {clients}")
    print("Pratiche per stato: " + ", ".join(f"{r['status']} {r['n']}" for r in practices))
    print("Stato pagamento: " + ", ".join(f"{r['payment_status']} {r['n']}" for r in payments))
    if requests:
        print("Richieste portale: " + ", ".join(f"{r['public_status']} {r['n']}" for r in requests))
    print(f"Portale /partner: utenti {', '.join(partner_users)} (password di prova: {PARTNER_PASSWORD})")


if __name__ == "__main__":
    main()
