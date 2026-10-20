import { useEffect, type ReactNode } from "react";

// Overlay modale condiviso (V1: .payment-popover/.payment-dialog) - stesso
// linguaggio visivo per tutti i flussi che in V1 usano un vero popup
// invece di window.confirm/window.prompt o form sempre visibili inline.
// Un solo componente riusato ovunque (registrazione pagamento, storno,
// eliminazioni/conferme), non una implementazione diversa per flusso.
export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="payment-popover"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="payment-dialog" role="dialog" aria-modal="true" aria-label={title}>
        {/* V1 (payment-popover/payment-dialog, es. app.py:14707): il popup
            ha sempre un .titlebar con h2 a sinistra e un bottone "Chiudi"
            visibile a destra - non solo backdrop/Escape. */}
        <div className="titlebar">
          <div>{title && <h2>{title}</h2>}</div>
          <button className="btn-ghost" type="button" onClick={onClose}>
            Chiudi
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
