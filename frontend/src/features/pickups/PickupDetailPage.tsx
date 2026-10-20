import type { ReactNode } from "react";
import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ChevronRightIcon, ClipboardIcon, ClockIcon, HomeIcon, MessageIcon, MoreVerticalIcon, NavigationIcon, PawIcon, PhoneIcon, ReceiptIcon, UserIcon } from "@/app/icons";
import { useCompanyLocations } from "@/features/practices/api";
import { pickupStatusStyle } from "@/shared/pickupStatusColor";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import "./pickupsDeliveries.css";

import {
  useCancelPickup,
  useCancelPickupAndTrashPractice,
  useCreatePracticeFromPickup,
  usePickup,
  useTransitionPickup,
  type PickupStatusValue,
} from "./api";

const STATUS_LABELS: Record<string, string> = {
  da_confermare: "Da confermare",
  da_ritirare: "Da ritirare",
  ritirato: "Ritirato",
  annullato: "Annullato",
};

const PICKUP_TYPE_LABELS: Record<string, string> = {
  sede_aziendale: "Sede aziendale",
  domicilio: "Domicilio",
  veterinario: "Veterinario",
  collaboratore: "Collaboratore",
  altro: "Altro",
};

// Stesso grafo di domain/pickup/state_machine.py, solo per decidere quali
// opzioni proporre nel form di cambio stato - la validazione reale resta
// sempre lato backend.
const ALLOWED_NEXT: Record<string, PickupStatusValue[]> = {
  da_confermare: ["da_ritirare", "annullato"],
  da_ritirare: ["ritirato", "annullato"],
  ritirato: ["annullato"],
  annullato: [],
};

// V1 (wa_digits, app.py:16016): stessa normalizzazione (prefisso 39 se
// manca, rimozione 00 iniziale) - duplicata qui come in
// features/practices/PracticeDetailPage.tsx (nessun helper condiviso).
function waDigits(phone: string): string {
  let digits = phone.replace(/\D+/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits && !digits.startsWith("39")) digits = "39" + digits;
  return digits;
}

// V1 (calendar_event_detail, app.py:11225-11233 hero_row): riga icona +
// testo dentro la hero card del riepilogo.
function HeroRow({ icon, color, main, sub }: { icon: ReactNode; color: string; main: string; sub?: string }) {
  return (
    <div className="calendar-detail-hero-meta-item">
      <span className={`calendar-detail-hero-meta-icon calendar-icon-${color}`}>{icon}</span>
      <div>
        <b>{main}</b>
        {sub && <small>{sub}</small>}
      </div>
    </div>
  );
}

export function PickupDetailPage() {
  const { pickupId } = useParams();
  const navigate = useNavigate();
  const id = Number(pickupId);
  const { data: pickup, isLoading, isError } = usePickup(id);
  const transition = useTransitionPickup();
  const cancel = useCancelPickup();
  const cancelAndTrash = useCancelPickupAndTrashPractice();
  const createPractice = useCreatePracticeFromPickup();
  const { data: locations } = useCompanyLocations();
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelModalOpen, setCancelModalOpen] = useState(false);
  const [cancelAndTrashModalOpen, setCancelAndTrashModalOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [statusEditOpen, setStatusEditOpen] = useState(false);
  // Continuità di navigazione: se l'utente apre il form "Crea pratica",
  // sceglie una sede e poi segue un link verso un'entità collegata
  // tornando con Indietro, deve ritrovare il form ancora aperto e la sede
  // ancora selezionata - rappresentati nell'URL, non in uno useState che
  // uno smontaggio di route cancellerebbe.
  const [searchParams, setSearchParams] = useSearchParams();
  const showCreatePracticeForm = searchParams.get("crea_pratica") === "1";
  const destinationBranchId = Number(searchParams.get("sede")) || "";

  function setShowCreatePracticeForm(value: boolean) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("crea_pratica", "1");
        else {
          next.delete("crea_pratica");
          next.delete("sede");
        }
        return next;
      },
      { replace: true },
    );
  }

  function setDestinationBranchId(value: number | "") {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("sede", String(value));
        else next.delete("sede");
        return next;
      },
      { replace: true },
    );
  }

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !pickup) return <p className="error-banner">Ritiro non trovato.</p>;

  async function handleTransition(target: PickupStatusValue) {
    setActionError(null);
    try {
      await transition.mutateAsync({ pickupId: id, targetStatus: target });
      setStatusEditOpen(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleCancel(reason: string) {
    setActionError(null);
    try {
      await cancel.mutateAsync({ pickupId: id, reason: reason || undefined });
      setCancelModalOpen(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleCancelAndTrashPractice(reason: string) {
    setActionError(null);
    try {
      await cancelAndTrash.mutateAsync({ pickupId: id, reason });
      setCancelAndTrashModalOpen(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleCreatePractice() {
    if (!destinationBranchId) return;
    setActionError(null);
    try {
      const practice = await createPractice.mutateAsync({
        pickupId: id,
        destinationBranchId: Number(destinationBranchId),
        serviceType: "Da decidere",
      });
      navigate(`/pratiche/${practice.id}`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  const nextStates = ALLOWED_NEXT[pickup.pickup_status].filter((s) => s !== "annullato");
  const canCreatePractice = pickup.pickup_status === "ritirato" && !pickup.linked_practice_id;
  const canCancel = pickup.pickup_status !== "annullato";

  const animalNames = pickup.animals.map((a) => a.name ?? "-").join(", ") || "Nessun animale";
  const startDate = new Date(pickup.start_at);
  const endDate = new Date(pickup.end_at);
  const timeText = startDate.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  const endTimeText = endDate.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  const dateText = startDate.toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" });
  const phone = pickup.client_phone?.trim() ?? "";
  const tel = phone.replace(/[^0-9+]/g, "");
  const wa = phone ? waDigits(phone) : "";
  const address = pickup.pickup_address ?? "";

  return (
    <main className="wrap calendar-wrap calendar-detail-v2">
      {actionError && <p className="error-banner">{actionError}</p>}
      {pickup.linked_practice_id && pickup.pickup_status === "annullato" && (
        <p className="flash warning">
          Collegato alla pratica #{pickup.linked_practice_id} - il ritiro e' annullato ma la pratica NON e' stata modificata.
        </p>
      )}

      <div className="calendar-detail-topbar">
        <button type="button" className="calendar-detail-back" onClick={() => navigate("/ritiri")}>
          <span className="calendar-detail-back-arrow">
            <ChevronRightIcon />
          </span>
          <span>Indietro</span>
        </button>
        <h2 className="calendar-detail-topbar-title">Riepilogo ritiro</h2>
        <div className="calendar-appt-menu-wrap calendar-detail-topbar-menu">
          <button type="button" className="calendar-detail-topbar-menu-btn" aria-label="Altre azioni" onClick={() => setMenuOpen((v) => !v)}>
            <MoreVerticalIcon />
          </button>
          {menuOpen && (
            <div className="calendar-appt-menu-popover">
              {canCancel && (
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    setCancelModalOpen(true);
                  }}
                >
                  Annulla ritiro
                </button>
              )}
              {pickup.linked_practice_id && canCancel && (
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    setCancelAndTrashModalOpen(true);
                  }}
                >
                  Annulla ritiro e cestina la pratica
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="calendar-detail-hero calendar-hero-pink">
        <div className="calendar-detail-hero-top">
          <span className="calendar-detail-header-icon calendar-icon-pink">
            <PawIcon />
          </span>
          <div className="calendar-detail-hero-titles">
            <span className="calendar-detail-hero-eyebrow calendar-hero-text-pink">RITIRO</span>
            <h1>{PICKUP_TYPE_LABELS[pickup.pickup_type] ?? pickup.pickup_type}</h1>
            <div
              className={`calendar-detail-status-quickedit calendar-quickedit-card${statusEditOpen ? " expanded" : ""}`}
              onClick={() => nextStates.length > 0 && setStatusEditOpen((v) => !v)}
            >
              <span className="calendar-detail-status-badge" style={pickupStatusStyle(pickup.pickup_status)}>
                {STATUS_LABELS[pickup.pickup_status].toUpperCase()}
                {nextStates.length > 0 && <ChevronRightIcon style={{ transform: "rotate(90deg)" }} />}
              </span>
              {nextStates.length > 0 && (
                <div className="calendar-quickedit-form" onClick={(e) => e.stopPropagation()}>
                  <select
                    value=""
                    disabled={transition.isPending}
                    onChange={(e) => {
                      if (e.target.value) handleTransition(e.target.value as PickupStatusValue);
                    }}
                  >
                    <option value="">Porta a...</option>
                    {nextStates.map((target) => (
                      <option key={target} value={target}>
                        {STATUS_LABELS[target]}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="calendar-detail-hero-meta">
          <HeroRow icon={<ClockIcon />} color="purple" main={`${timeText} → ${endTimeText}`} sub={dateText} />
          <HeroRow icon={<HomeIcon />} color="teal" main={pickup.pickup_contact_name || address || "Luogo non impostato"} sub={pickup.pickup_contact_name ? address : undefined} />
          <HeroRow icon={<PawIcon />} color="amber" main={animalNames} />
          {pickup.client_name && <HeroRow icon={<UserIcon />} color="blue" main={pickup.client_name} sub={pickup.client_phone ?? undefined} />}
          {pickup.notes && <HeroRow icon={<ClipboardIcon />} color="purple" main={pickup.notes.split("\n")[0].slice(0, 80)} />}
        </div>
      </div>

      <div className="calendar-detail-quickactions">
        {tel ? (
          <a className="calendar-detail-qa" href={`tel:${tel}`}>
            <span className="calendar-detail-qa-icon">
              <PhoneIcon />
            </span>
            <span>Chiama</span>
          </a>
        ) : (
          <span className="calendar-detail-qa calendar-detail-qa-disabled">
            <span className="calendar-detail-qa-icon">
              <PhoneIcon />
            </span>
            <span>Chiama</span>
          </span>
        )}
        {wa ? (
          <a className="calendar-detail-qa" href={`https://wa.me/${wa}`} target="_blank" rel="noopener noreferrer">
            <span className="calendar-detail-qa-icon">
              <MessageIcon />
            </span>
            <span>WhatsApp</span>
          </a>
        ) : (
          <span className="calendar-detail-qa calendar-detail-qa-disabled">
            <span className="calendar-detail-qa-icon">
              <MessageIcon />
            </span>
            <span>WhatsApp</span>
          </span>
        )}
        {address ? (
          <a
            className="calendar-detail-qa"
            href={`https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(address)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            <span className="calendar-detail-qa-icon">
              <NavigationIcon />
            </span>
            <span>Naviga</span>
          </a>
        ) : (
          <span className="calendar-detail-qa calendar-detail-qa-disabled">
            <span className="calendar-detail-qa-icon">
              <NavigationIcon />
            </span>
            <span>Naviga</span>
          </span>
        )}
        {pickup.linked_practice_id ? (
          <a className="calendar-detail-qa" href={`/pratiche/${pickup.linked_practice_id}`}>
            <span className="calendar-detail-qa-icon">
              <ReceiptIcon />
            </span>
            <span>Apri pratica</span>
          </a>
        ) : (
          <span className="calendar-detail-qa calendar-detail-qa-disabled">
            <span className="calendar-detail-qa-icon">
              <ReceiptIcon />
            </span>
            <span>Pratica</span>
          </span>
        )}
      </div>

      {canCreatePractice && (
        <div className="calendar-card-list">
          {showCreatePracticeForm ? (
            <div className="calendar-tap-card lookup">
              <span className="calendar-tap-card-icon">
                <ReceiptIcon />
              </span>
              <div className="calendar-tap-card-body">
                <small>Crea pratica da questo ritiro</small>
                <select value={destinationBranchId} onChange={(e) => setDestinationBranchId(Number(e.target.value) || "")}>
                  <option value="">Seleziona sede...</option>
                  {locations?.map((loc) => (
                    <option key={loc.id} value={loc.id}>
                      {loc.name}
                    </option>
                  ))}
                </select>
                <button className="btn" style={{ marginTop: 10 }} disabled={!destinationBranchId || createPractice.isPending} onClick={handleCreatePractice}>
                  Conferma creazione
                </button>
              </div>
            </div>
          ) : (
            <button type="button" className="calendar-tap-card lookup" aria-label="Crea pratica" onClick={() => setShowCreatePracticeForm(true)}>
              <span className="calendar-tap-card-icon">
                <ReceiptIcon />
              </span>
              <div className="calendar-tap-card-body">
                <small>Nessuna pratica collegata</small>
                <p>Crea pratica</p>
              </div>
            </button>
          )}
        </div>
      )}

      <ConfirmModal
        open={cancelModalOpen}
        onClose={() => setCancelModalOpen(false)}
        onConfirm={handleCancel}
        title="Annulla ritiro"
        reasonLabel="Motivo dell'annullamento (opzionale)"
        confirmLabel="Annulla ritiro"
        danger
        isPending={cancel.isPending}
      />

      <ConfirmModal
        open={cancelAndTrashModalOpen}
        onClose={() => setCancelAndTrashModalOpen(false)}
        onConfirm={handleCancelAndTrashPractice}
        title="Annulla ritiro e cestina la pratica"
        description={`Verrà cestinata anche la pratica #${pickup.linked_practice_id} collegata.`}
        requireReason
        reasonLabel="Motivo"
        confirmLabel="Annulla e cestina"
        danger
        isPending={cancelAndTrash.isPending}
      />
    </main>
  );
}
