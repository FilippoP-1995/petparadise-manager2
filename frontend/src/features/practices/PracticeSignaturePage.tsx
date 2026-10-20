import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { usePractice, useUpdatePractice } from "./api";
import { practiceToUpdateInput } from "./practiceUpdateMapper";

// V1 (signature_page, app.py:17421-17432): canvas a schermo intero
// dedicato, stesso identico algoritmo di disegno (linea 3px, tondeggiante,
// mouse+touch, ridimensionato su devicePixelRatio) - portato 1:1 in
// React/canvas invece che script inline, nessun comportamento nuovo.
// Salva tramite lo stesso mapper sicuro di PracticeEditFormPage: solo
// signature_data cambia, line_items/tag/animali restano esattamente quelli
// gia' caricati (V1 protegge signature_data dal form di modifica generale
// esattamente allo stesso modo, vedi practice_autosave "protected").
export function PracticeSignaturePage() {
  const { practiceId } = useParams();
  const navigate = useNavigate();
  const id = Number(practiceId);
  const { data: practice, isLoading, isError } = usePractice(id);
  const update = useUpdatePractice();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const lastRef = useRef<{ x: number; y: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasDrawn, setHasDrawn] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    function resize() {
      const r = canvas!.getBoundingClientRect();
      const d = window.devicePixelRatio || 1;
      canvas!.width = r.width * d;
      canvas!.height = r.height * d;
      ctx!.setTransform(d, 0, 0, d, 0, 0);
      ctx!.lineWidth = 3;
      ctx!.lineCap = "round";
      ctx!.strokeStyle = "#1f1f1f";
    }

    function pos(e: MouseEvent | TouchEvent) {
      const r = canvas!.getBoundingClientRect();
      const t = "touches" in e ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    }

    function start(e: MouseEvent | TouchEvent) {
      drawingRef.current = true;
      lastRef.current = pos(e);
      e.preventDefault();
    }
    function move(e: MouseEvent | TouchEvent) {
      if (!drawingRef.current || !lastRef.current) return;
      const p = pos(e);
      ctx!.beginPath();
      ctx!.moveTo(lastRef.current.x, lastRef.current.y);
      ctx!.lineTo(p.x, p.y);
      ctx!.stroke();
      lastRef.current = p;
      setHasDrawn(true);
      e.preventDefault();
    }
    function end(e: MouseEvent | TouchEvent) {
      drawingRef.current = false;
      e.preventDefault();
    }

    resize();
    window.addEventListener("resize", resize);
    canvas.addEventListener("mousedown", start);
    canvas.addEventListener("mousemove", move);
    canvas.addEventListener("mouseup", end);
    canvas.addEventListener("mouseleave", end);
    canvas.addEventListener("touchstart", start, { passive: false });
    canvas.addEventListener("touchmove", move, { passive: false });
    canvas.addEventListener("touchend", end, { passive: false });
    return () => {
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("mousedown", start);
      canvas.removeEventListener("mousemove", move);
      canvas.removeEventListener("mouseup", end);
      canvas.removeEventListener("mouseleave", end);
      canvas.removeEventListener("touchstart", start);
      canvas.removeEventListener("touchmove", move);
      canvas.removeEventListener("touchend", end);
    };
    // Il <canvas> esiste solo dopo che isLoading diventa false (il primo
    // render, mentre la pratica sta ancora caricando, ritorna prima del
    // JSX col canvas - senza questa dipendenza l'effect gira una volta
    // sola su quel primo render, quando canvasRef.current e' ancora null,
    // e i listener non verrebbero mai davvero collegati).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading]);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !practice) return <p className="error-banner">Pratica non trovata.</p>;

  function clearPad() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasDrawn(false);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!hasDrawn || !canvasRef.current) {
      setError("Firma non valida: prova a firmare di nuovo prima di salvare.");
      return;
    }
    const signatureData = canvasRef.current.toDataURL("image/png");
    try {
      await update.mutateAsync({
        practiceId: id,
        input: { ...practiceToUpdateInput(practice!), signature_data: signatureData },
      });
      navigate(`/pratiche/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore nel salvataggio della firma");
    }
  }

  const owner = [practice.owner_snapshot?.first_name, practice.owner_snapshot?.last_name].filter(Boolean).join(" ") || "-";

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Firma proprietario</h1>
          <div className="sub">
            {owner} - pratica {practice.practice_number}
          </div>
        </div>
      </div>

      {error && <div className="flash warning">{error}</div>}

      <section className="section">
        {/* V1 (signature_page, app.py:17421): il testo reale finisce con "e
            inserita nel PDF DDT" - qui omesso di proposito: il salvataggio
            firma di V2 (practice_service.py) non rigenera nessun DDT PDF
            (nessuna generate_ddt/ddt_pdf_path toccata), quindi quella
            promessa sarebbe falsa in V2. Gap di dominio, non di sola
            presentazione - non improvvisabile senza il motore DDT. */}
        <p className="sub">Fai firmare qui il proprietario con il dito. La firma verra' salvata nella pratica.</p>
        <form onSubmit={handleSubmit}>
          <canvas className="signature-pad" ref={canvasRef} />
          <div className="actions" style={{ marginTop: 14 }}>
            <button className="btn" type="submit" disabled={update.isPending}>
              {update.isPending ? "Salvataggio..." : "Salva firma"}
            </button>
            <button className="btn-ghost" type="button" onClick={clearPad}>
              Cancella
            </button>
            <button className="btn-ghost" type="button" onClick={() => navigate(`/pratiche/${id}`)}>
              Annulla
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}
