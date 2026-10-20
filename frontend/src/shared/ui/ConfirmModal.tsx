import { useId, useState, type FormEvent } from "react";

import { Modal } from "./Modal";

// Conferma/motivo condivisa per i flussi che in V1 sono un vero popup
// (o che qui sostituiscono window.confirm/window.prompt con lo stesso
// linguaggio visivo del resto della V2, invece di un dialogo nativo del
// browser) - storno, eliminazione, cestinazione pratica: stesso
// componente, non tre implementazioni diverse.
export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  requireReason,
  reasonLabel = "Motivo",
  confirmLabel = "Conferma",
  danger,
  isPending,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  title: string;
  description?: string;
  requireReason?: boolean;
  reasonLabel?: string;
  confirmLabel?: string;
  danger?: boolean;
  isPending?: boolean;
  error?: string | null;
}) {
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const reasonId = useId();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (requireReason && !reason.trim()) {
      setLocalError("Il motivo è obbligatorio.");
      return;
    }
    setLocalError(null);
    onConfirm(reason.trim());
  }

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <form onSubmit={handleSubmit}>
        {description && <p className="sub">{description}</p>}
        {requireReason && (
          <div className="field full" style={{ marginTop: 12 }}>
            <label htmlFor={reasonId}>{reasonLabel}</label>
            <textarea id={reasonId} value={reason} onChange={(e) => setReason(e.target.value)} rows={3} autoFocus />
          </div>
        )}
        {(localError || error) && <p className="field-error">{localError || error}</p>}
        <div className="actions" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Annulla
          </button>
          <button type="submit" className={danger ? "btn danger-btn" : "btn"} disabled={isPending}>
            {isPending ? "Attendere..." : confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
