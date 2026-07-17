"""Isolated operational-calendar schema and domain helpers."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta


EVENT_TYPES = ("Ritiro", "Ritiro in sede", "Riconsegna", "Riconsegna in sede", "Appuntamento")
PICKUP_STATUSES = ("Da confermare", "Da ritirare", "Ritirato", "Annullato")
DELIVERY_STATUSES = ("In programma", "Completato")
PAYMENT_STATUSES = ("Da pagare", "Da saldare", "Pagato")
CALENDAR_OPERATORS = ("Serena", "Alessio", "Filippo", "Gianluca")
DEFAULT_ZONES = (
    "Livorno", "Empoli", "Pisa", "Viareggio", "Firenze", "Sesto Fiorentino",
    "Montelupo", "Pietrasanta", "Lucca", "Castelfiorentino", "Pontedera", "San Miniato",
)


def ensure_calendar_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS calendar_events (
      id INTEGER PRIMARY KEY,
      event_type TEXT NOT NULL,
      title TEXT NOT NULL,
      zone TEXT,
      location_type TEXT,
      venue_name TEXT,
      address TEXT,
      phone TEXT,
      veterinarian_id INTEGER REFERENCES veterinarians(id) ON DELETE SET NULL,
      veterinarian_name TEXT,
      veterinarian_phone TEXT,
      veterinarian_address TEXT,
      veterinarian_hours TEXT,
      veterinarian_contact TEXT,
      client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
      client_first_name TEXT,
      client_last_name TEXT,
      client_phone TEXT,
      destination_site TEXT,
      animal_name TEXT,
      person_company TEXT,
      category TEXT,
      start_at TEXT NOT NULL,
      end_at TEXT NOT NULL,
      all_day INTEGER NOT NULL DEFAULT 0,
      event_status TEXT,
      payment_status TEXT,
      payment_amount REAL NOT NULL DEFAULT 0,
      notes TEXT,
      operator_name TEXT,
      created_by INTEGER NOT NULL REFERENCES users(id),
      assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      linked_practice_id INTEGER REFERENCES practices(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      updated_by INTEGER REFERENCES users(id),
      deleted_at TEXT,
      deleted_by INTEGER REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS calendar_event_animals (
      id INTEGER PRIMARY KEY,
      event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
      name TEXT, species TEXT, weight TEXT, cremation_type TEXT, notes TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS calendar_event_estimate_items (
      id INTEGER PRIMARY KEY,
      event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
      description TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS calendar_event_comments (
      id INTEGER PRIMARY KEY,
      event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id), message TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT, deleted_at TEXT, deleted_by INTEGER REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS calendar_event_history (
      id INTEGER PRIMARY KEY,
      event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
      user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, old_value TEXT, new_value TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS calendar_event_notifications (
      id INTEGER PRIMARY KEY,
      event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
      notification_type TEXT NOT NULL, scheduled_at TEXT NOT NULL, sent_at TEXT,
      status TEXT NOT NULL DEFAULT 'programmato', error TEXT,
      UNIQUE(event_id,notification_type,scheduled_at)
    );
    CREATE TABLE IF NOT EXISTS calendar_zones (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
      is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_calendar_events_range ON calendar_events(start_at,end_at,deleted_at);
    CREATE INDEX IF NOT EXISTS idx_calendar_events_type ON calendar_events(event_type,start_at);
    CREATE INDEX IF NOT EXISTS idx_calendar_events_status ON calendar_events(event_status,start_at);
    CREATE INDEX IF NOT EXISTS idx_calendar_events_vet ON calendar_events(veterinarian_id,start_at);
    CREATE INDEX IF NOT EXISTS idx_calendar_events_assigned ON calendar_events(assigned_user_id,start_at);
    CREATE INDEX IF NOT EXISTS idx_calendar_animals_event ON calendar_event_animals(event_id);
    CREATE INDEX IF NOT EXISTS idx_calendar_comments_event ON calendar_event_comments(event_id,created_at);
    CREATE INDEX IF NOT EXISTS idx_calendar_history_event ON calendar_event_history(event_id,created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_calendar_notifications_due ON calendar_event_notifications(status,scheduled_at);
    """)
    columns={row[1] for row in conn.execute("PRAGMA table_info(calendar_events)")}
    if "operator_name" not in columns:conn.execute("ALTER TABLE calendar_events ADD COLUMN operator_name TEXT")
    delivery_clinic_columns={
        "delivery_clinic_id":"INTEGER REFERENCES veterinarians(id) ON DELETE SET NULL",
        "delivery_clinic_name":"TEXT",
        "delivery_clinic_address":"TEXT",
        "delivery_clinic_phone":"TEXT",
    }
    for name,definition in delivery_clinic_columns.items():
        if name not in columns:conn.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {definition}")
    stamp=datetime.now().isoformat(timespec="seconds")
    conn.executemany("INSERT OR IGNORE INTO calendar_zones(name,is_default,created_at) VALUES(?,1,?)",((zone,stamp) for zone in DEFAULT_ZONES))


def period_bounds(view, selected):
    day=date.fromisoformat(selected)
    if view in ("settimana", "mista_settimana"):
        start=day-timedelta(days=day.weekday());end=start+timedelta(days=6)
    elif view in ("mese", "mista_mese", "compatto"):
        start=day.replace(day=1);next_month=(start.replace(day=28)+timedelta(days=4)).replace(day=1);end=next_month-timedelta(days=1)
    else:start=end=day
    return start,end


def overlap_rows(conn, start_date, end_date, filters=None, include_deleted=False):
    start=f"{start_date}T00:00:00";end=f"{end_date}T23:59:59"
    where=["e.start_at<=?", "e.end_at>=?"];args=[end,start]
    if not include_deleted:where.append("(e.deleted_at IS NULL OR e.deleted_at='')")
    filters=filters or {}
    exact={"event_type":"e.event_type","event_status":"e.event_status","operator_name":"e.operator_name","veterinarian_id":"e.veterinarian_id","location_type":"e.location_type"}
    for key,column in exact.items():
        value=str(filters.get(key) or "").strip()
        if value:where.append(f"{column}=?");args.append(value)
    zone=str(filters.get("zone") or "").strip()
    if zone:where.append("e.zone=?");args.append(zone)
    scope=str(filters.get("venue_scope") or "").strip()
    if scope=="sede":where.append("(e.event_type LIKE '%in sede' OR e.location_type IN ('Sede Livorno','Sede Empoli'))")
    elif scope=="fuori":where.append("(e.event_type NOT LIKE '%in sede' AND COALESCE(e.location_type,'') NOT IN ('Sede Livorno','Sede Empoli'))")
    date_from=str(filters.get("date_from") or "").strip()
    date_to=str(filters.get("date_to") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from):where.append("e.end_at>=?");args.append(date_from+"T00:00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to):where.append("e.start_at<=?");args.append(date_to+"T23:59:59")
    term=str(filters.get("q") or "").strip()
    if term:
        like=f"%{term}%";where.append("""(e.title LIKE ? OR e.animal_name LIKE ? OR COALESCE(e.client_first_name,'')||' '||COALESCE(e.client_last_name,'') LIKE ?
          OR e.veterinarian_name LIKE ? OR e.venue_name LIKE ? OR e.zone LIKE ? OR e.notes LIKE ?
          OR EXISTS(SELECT 1 FROM calendar_event_animals a WHERE a.event_id=e.id AND (a.name LIKE ? OR a.species LIKE ?)))""");args.extend([like]*9)
    return conn.execute(f"""SELECT e.*,u.display_name creator_name,au.display_name assigned_name,
      (SELECT count(*) FROM calendar_event_animals a WHERE a.event_id=e.id) animal_count,
      (SELECT COALESCE(sum(CASE WHEN trim(weight) GLOB '[0-9]*' THEN CAST(replace(weight,',','.') AS REAL) ELSE 0 END),0) FROM calendar_event_animals a WHERE a.event_id=e.id) animal_weight_total,
      (SELECT group_concat(DISTINCT cremation_type) FROM calendar_event_animals a WHERE a.event_id=e.id AND cremation_type!='') cremation_types,
      (SELECT COALESCE(sum(amount),0) FROM calendar_event_estimate_items i WHERE i.event_id=e.id) estimate_total
      FROM calendar_events e JOIN users u ON u.id=e.created_by LEFT JOIN users au ON au.id=e.assigned_user_id
      WHERE {' AND '.join(where)} ORDER BY e.start_at,e.id""",args).fetchall()


def _clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def automatic_title(event_type, zone="", animal="", site=""):
    zone=_clean(zone).upper();animal=_clean(animal).upper();site=_clean(site).upper()
    if event_type=="Ritiro":return f"RITIRO {zone}".strip()
    if event_type=="Ritiro in sede":return f"RITIRO IN SEDE {site}".strip()
    if event_type=="Riconsegna":return f"RICONSEGNA {animal}".strip()
    if event_type=="Riconsegna in sede":return f"RICONSEGNA {animal} IN SEDE {site}".strip()
    return ""


def normalize_time(value, default=""):
    value=re.sub(r"\D", "", str(value or ""))
    if not value:return default
    if len(value)<=2:
        hour=int(value)
        return f"{hour:02d}:00" if 0<=hour<=23 else ""
    if len(value)==3:value="0"+value
    if len(value)==4:
        hour=int(value[:2]);minute=int(value[2:])
        return f"{hour:02d}:{minute:02d}" if 0<=hour<=23 and 0<=minute<=59 else ""
    return ""


def normalize_event(form, current=None):
    event_type=_clean(form.get("event_type"),50)
    if event_type not in EVENT_TYPES:raise ValueError("Tipo evento non valido")
    all_day=form.get("all_day")=="1"
    start_date=_clean(form.get("start_date"),10);end_date=_clean(form.get("end_date"),10) or start_date
    try:date.fromisoformat(start_date);date.fromisoformat(end_date)
    except ValueError:raise ValueError("Inserisci date valide")
    start_time="00:00" if all_day else normalize_time(form.get("start_time"))
    end_time="23:59" if all_day else normalize_time(form.get("end_time"),start_time)
    if not all_day and (not re.fullmatch(r"\d{2}:\d{2}",start_time) or not re.fullmatch(r"\d{2}:\d{2}",end_time)):raise ValueError("Inserisci un orario valido")
    start_at=f"{start_date}T{start_time}:00";end_at=f"{end_date}T{end_time}:59"
    if end_at<start_at:raise ValueError("La fine dell'evento non può precedere l'inizio")
    zone=_clean(form.get("zone"),100);site=_clean(form.get("destination_site"),50);animal=_clean(form.get("animal_name"),100)
    if event_type not in ("Ritiro","Riconsegna"):zone=""
    title=_clean(form.get("title"),200) or automatic_title(event_type,zone,animal,site)
    if not title:raise ValueError("Il titolo è obbligatorio")
    if event_type=="Ritiro" and not zone:raise ValueError("La zona è obbligatoria")
    if event_type in ("Ritiro in sede","Riconsegna in sede") and site not in ("Livorno","Empoli"):raise ValueError("Seleziona la sede")
    if event_type in ("Riconsegna","Riconsegna in sede") and not animal:raise ValueError("Il nome animale è obbligatorio")
    operator=_clean(form.get("operator_name"),50).title()
    if operator not in CALENDAR_OPERATORS:raise ValueError("Seleziona l'operatore")
    status=_clean(form.get("event_status"),50)
    if event_type in ("Ritiro","Ritiro in sede"):
        status=status if status in PICKUP_STATUSES else "Da confermare"
    elif event_type in ("Riconsegna","Riconsegna in sede"):
        status=status if status in DELIVERY_STATUSES else "In programma"
    else:status=""
    payment=_clean(form.get("payment_status"),50)
    if event_type not in ("Riconsegna","Riconsegna in sede"):payment=""
    elif payment not in PAYMENT_STATUSES:payment="Da pagare"
    try:amount=float(str(form.get("payment_amount") or "0").replace(",","."))
    except ValueError:raise ValueError("Importo non valido")
    return {
      "event_type":event_type,"title":title,"zone":zone,"location_type":_clean(form.get("location_type"),50),
      "venue_name":_clean(form.get("venue_name"),200),"address":_clean(form.get("delivery_address") or form.get("address"),500),"phone":_clean(form.get("phone"),50),
      "veterinarian_id":int(form["veterinarian_id"]) if str(form.get("veterinarian_id") or "").isdigit() else None,
      "veterinarian_name":_clean(form.get("veterinarian_name"),200),"veterinarian_phone":_clean(form.get("veterinarian_phone"),50),
      "veterinarian_address":_clean(form.get("veterinarian_address"),500),"veterinarian_hours":_clean(form.get("veterinarian_hours"),500),
      "veterinarian_contact":_clean(form.get("veterinarian_contact"),200),
      "client_id":int(form["client_id"]) if str(form.get("client_id") or "").isdigit() else None,
      "client_first_name":_clean(form.get("client_first_name"),100),"client_last_name":_clean(form.get("client_last_name"),100),
      "client_phone":_clean(form.get("client_phone"),50),"destination_site":site,"animal_name":animal,
      "person_company":"","category":_clean(form.get("category"),100),
      "start_at":start_at,"end_at":end_at,"all_day":1 if all_day else 0,"event_status":status,
      "payment_status":payment,"payment_amount":max(0,amount),"notes":str(form.get("notes") or "").strip()[:5000],
      "operator_name":operator,"assigned_user_id":None,
      "delivery_clinic_id":int(form["delivery_clinic_id"]) if str(form.get("delivery_clinic_id") or "").isdigit() else None,
      "delivery_clinic_name":_clean(form.get("delivery_clinic_name"),200) if event_type in ("Riconsegna","Riconsegna in sede") else "",
      "delivery_clinic_address":_clean(form.get("delivery_clinic_address"),500) if event_type in ("Riconsegna","Riconsegna in sede") else "",
      "delivery_clinic_phone":_clean(form.get("delivery_clinic_phone"),50) if event_type in ("Riconsegna","Riconsegna in sede") else "",
    }


def parse_items(raw, kind):
    try:items=json.loads(raw or "[]")
    except (ValueError,TypeError):raise ValueError("Dati elenco non validi")
    if not isinstance(items,list) or len(items)>50:raise ValueError("Dati elenco non validi")
    cleaned=[]
    for item in items:
        if not isinstance(item,dict):continue
        if kind=="animal":
            cremation=_clean(item.get("cremation_type"),30)
            animal={"name":_clean(item.get("name"),100),"species":_clean(item.get("species"),100),"weight":_clean(item.get("weight"),30),"cremation_type":cremation if cremation in ("Singola","Collettiva") else "","notes":_clean(item.get("notes"),500)}
            if any(animal.values()):cleaned.append(animal)
        else:
            description=_clean(item.get("description"),200)
            if not description:continue
            try:amount=max(0,float(str(item.get("amount") or 0).replace(",",".")))
            except ValueError:raise ValueError("Importo preventivo non valido")
            cleaned.append({"description":description,"amount":amount})
    return cleaned


def event_color_class(row):
    if row["event_type"]=="Appuntamento":return "calendar-purple"
    if row["event_type"] in ("Riconsegna","Riconsegna in sede"):return "calendar-blue"
    return {"Da confermare":"calendar-yellow","Da ritirare":"calendar-red","Ritirato":"calendar-green","Annullato":"calendar-dark"}.get(row["event_status"],"calendar-red")


def event_type_dot_class(row):
    return {"Ritiro":"calendar-dot-red","Ritiro in sede":"calendar-dot-yellow","Riconsegna":"calendar-dot-blue","Riconsegna in sede":"calendar-dot-cyan","Appuntamento":"calendar-dot-purple"}.get(row["event_type"],"calendar-dot-gray")


def add_history(conn,event_id,user_id,action,old_value="",new_value="",stamp=None):
    conn.execute("INSERT INTO calendar_event_history(event_id,user_id,action,old_value,new_value,created_at) VALUES(?,?,?,?,?,?)",(event_id,user_id,action,str(old_value or "")[:2000],str(new_value or "")[:2000],stamp or datetime.now().isoformat(timespec="seconds")))


def sync_children(conn,event_id,animals,estimates,stamp):
    conn.execute("DELETE FROM calendar_event_animals WHERE event_id=?",(event_id,))
    conn.execute("DELETE FROM calendar_event_estimate_items WHERE event_id=?",(event_id,))
    conn.executemany("INSERT INTO calendar_event_animals(event_id,name,species,weight,cremation_type,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",((event_id,a["name"],a["species"],a["weight"],a["cremation_type"],a["notes"],stamp,stamp) for a in animals))
    conn.executemany("INSERT INTO calendar_event_estimate_items(event_id,description,amount,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",((event_id,i["description"],i["amount"],index,stamp,stamp) for index,i in enumerate(estimates)))


def schedule_event_notifications(conn,event_id,start_at,stamp):
    try:reminder=(datetime.fromisoformat(start_at)-timedelta(minutes=30)).isoformat(timespec="seconds")
    except ValueError:return
    conn.execute("DELETE FROM calendar_event_notifications WHERE event_id=? AND notification_type='calendar_reminder_30m' AND sent_at IS NULL",(event_id,))
    conn.execute("INSERT OR IGNORE INTO calendar_event_notifications(event_id,notification_type,scheduled_at,status) VALUES(?,?,?,'programmato')",(event_id,"calendar_reminder_30m",reminder))
