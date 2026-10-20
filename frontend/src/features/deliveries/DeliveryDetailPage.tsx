import type { ReactNode } from "react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ChevronRightIcon, ClipboardIcon, ClockIcon, HomeIcon, MessageIcon, NavigationIcon, PackageIcon, PhoneIcon, ReceiptIcon, UserIcon, WalletIcon } from "@/app/icons";
import { formatMoney } from "@/shared/money";

import "../pickups/pickupsDeliveries.css";

import { useDelivery, useLinkDeliveryToPractice } from "./api";

const DELIVERY_TYPE_LABELS: Record<string, string> = {
  sede_aziendale: "Sede aziendale",
  domicilio: "Domicilio",
  ambulatorio: "Ambulatorio",
  altro: "Altro",
};

// V1 (wa_digits, app.py:16016): stessa normalizzazione, duplicata come in
// features/pickups/PickupDetailPage.tsx e features/practices/PracticeDetailPage.tsx
// (nessun helper condiviso nel repo).
function waDigits(phone: string): string {
  let digits = phone.replace(/\D+/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits && !digits.startsWith("39")) digits = "39" + digits;
  return digits;
}

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

export function DeliveryDetailPage() {
  const { deliveryId } = useParams();
  const navigate = useNavigate();
  const id = Number(deliveryId);
  const { data: delivery, isLoading, isError } = useDelivery(id);
  const link = useLinkDeliveryToPractice();
  const [practiceIdInput, setPracticeIdInput] = useState("");
  const [mismatchError, setMismatchError] = useState<string | null>(null);
  const [showLinkForm, setShowLinkForm] = useState(false);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !delivery) return <p className="error-banner">Riconsegna non trovata.</p>;

  async function handleLink(confirmDespiteMismatch: boolean) {
    const practiceId = Number(practiceIdInput);
    if (!practiceId) return;
    setMismatchError(null);
    try {
      await link.mutateAsync({ deliveryId: id, practiceId, confirmDespiteMismatch });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Operazione non riuscita";
      setMismatchError(message);
    }
  }

  const startDate = new Date(delivery.start_at);
  const endDate = new Date(delivery.end_at);
  const timeText = startDate.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  const endTimeText = endDate.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  const dateText = startDate.toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" });
  const phone = delivery.client_phone?.trim() ?? "";
  const tel = phone.replace(/[^0-9+]/g, "");
  const wa = phone ? waDigits(phone) : "";
  const address = delivery.delivery_address ?? "";
  const paymentText = delivery.preliminary_payment_status
    ? `${delivery.preliminary_payment_status}${delivery.preliminary_payment_amount != null ? ` (${formatMoney(delivery.preliminary_payment_amount)})` : ""}`
    : "Nessuno";

  return (
    <main className="wrap calendar-wrap calendar-detail-v2">
      <div className="calendar-detail-topbar">
        <button type="button" className="calendar-detail-back" onClick={() => navigate("/riconsegne")}>
          <span className="calendar-detail-back-arrow">
            <ChevronRightIcon />
          </span>
          <span>Indietro</span>
        </button>
        <h2 className="calendar-detail-topbar-title">Riepilogo riconsegna</h2>
      </div>

      <div className="calendar-detail-hero calendar-hero-blue">
        <div className="calendar-detail-hero-top">
          <span className="calendar-detail-header-icon calendar-icon-blue">
            <PackageIcon />
          </span>
          <div className="calendar-detail-hero-titles">
            <span className="calendar-detail-hero-eyebrow calendar-hero-text-blue">RICONSEGNA</span>
            <h1>{DELIVERY_TYPE_LABELS[delivery.delivery_type] ?? delivery.delivery_type}</h1>
            {/* V1 (calendar_event_detail, app.py:11200-11202): le riconsegne non
                hanno uno stato gestibile dall'utente (decisione esplicita) -
                nessun badge di stato qui, come in V1. */}
          </div>
        </div>
        <div className="calendar-detail-hero-meta">
          <HeroRow icon={<ClockIcon />} color="purple" main={`${timeText} → ${endTimeText}`} sub={dateText} />
          <HeroRow icon={<HomeIcon />} color="teal" main={address || "Luogo non impostato"} />
          <HeroRow icon={<WalletIcon />} color="amber" main={paymentText} sub="Pagamento preliminare" />
          {delivery.client_name && <HeroRow icon={<UserIcon />} color="blue" main={delivery.client_name} sub={delivery.client_phone ?? undefined} />}
          {delivery.notes && <HeroRow icon={<ClipboardIcon />} color="purple" main={delivery.notes.split("\n")[0].slice(0, 80)} />}
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
        {delivery.linked_practice_id ? (
          <a className="calendar-detail-qa" href={`/pratiche/${delivery.linked_practice_id}`}>
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

      {!delivery.linked_practice_id && (
        <div className="calendar-card-list">
          {showLinkForm ? (
            <div className="calendar-tap-card lookup">
              <span className="calendar-tap-card-icon">
                <ReceiptIcon />
              </span>
              <div className="calendar-tap-card-body">
                <small>Collega pratica esistente</small>
                <input placeholder="ID pratica" value={practiceIdInput} onChange={(e) => setPracticeIdInput(e.target.value)} />
                <button className="btn" style={{ marginTop: 10 }} disabled={link.isPending} onClick={() => handleLink(false)}>
                  Collega
                </button>
                {mismatchError && (
                  <div className="flash warning" style={{ marginTop: 10 }}>
                    <p>{mismatchError}</p>
                    <button className="btn-ghost" onClick={() => handleLink(true)}>
                      Conferma comunque il collegamento
                    </button>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <button type="button" className="calendar-tap-card lookup" aria-label="Collega a una pratica" onClick={() => setShowLinkForm(true)}>
              <span className="calendar-tap-card-icon">
                <ReceiptIcon />
              </span>
              <div className="calendar-tap-card-body">
                <small>Nessuna pratica collegata</small>
                <p>Collega a una pratica</p>
              </div>
            </button>
          )}
        </div>
      )}
    </main>
  );
}
