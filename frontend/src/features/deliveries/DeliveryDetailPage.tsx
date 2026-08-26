import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { formatMoney } from "@/shared/money";

import { useDelivery, useLinkDeliveryToPractice } from "./api";

export function DeliveryDetailPage() {
  const { deliveryId } = useParams();
  const navigate = useNavigate();
  const id = Number(deliveryId);
  const { data: delivery, isLoading, isError } = useDelivery(id);
  const link = useLinkDeliveryToPractice();
  const [practiceIdInput, setPracticeIdInput] = useState("");
  const [mismatchError, setMismatchError] = useState<string | null>(null);

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

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Riconsegna {new Date(delivery.start_at).toLocaleString("it-IT")}</h1>
      </div>

      <div className="card">
        <p>
          <strong>Modalita':</strong> {delivery.delivery_type}
        </p>
        <p>
          <strong>Pagamento preliminare:</strong> {delivery.preliminary_payment_status ?? "-"}
          {delivery.preliminary_payment_amount != null && ` (${formatMoney(delivery.preliminary_payment_amount)})`}
        </p>
        <p>
          <strong>Pratica collegata:</strong> {delivery.linked_practice_id ? `#${delivery.linked_practice_id}` : "Nessuna"}
        </p>
      </div>

      {!delivery.linked_practice_id && (
        <div className="card">
          <h2>Collega a una pratica</h2>
          <div className="field-row">
            <input
              placeholder="ID pratica"
              value={practiceIdInput}
              onChange={(e) => setPracticeIdInput(e.target.value)}
            />
            <button className="btn" disabled={link.isPending} onClick={() => handleLink(false)}>
              Collega
            </button>
          </div>
          {mismatchError && (
            <div className="flash warning">
              <p>{mismatchError}</p>
              <button className="btn-ghost" onClick={() => handleLink(true)}>
                Conferma comunque il collegamento
              </button>
            </div>
          )}
        </div>
      )}

      <div className="actions">
        <button className="btn-ghost" onClick={() => navigate("/riconsegne")}>
          Torna all'elenco
        </button>
      </div>
    </main>
  );
}
