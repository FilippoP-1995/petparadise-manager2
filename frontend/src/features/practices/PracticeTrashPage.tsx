import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useVeterinarians } from "@/features/veterinarians/api";
import { speciesAvatar } from "@/shared/speciesAvatar";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import { useRestorePractice, useTrashedPractices, type Practice } from "./api";

// V1 (trash_page, app.py:18307-18332): stessa nota, stessa tabella
// (Data recupero/Pratica/Animale/Speditore/Veterinario/Azioni), stesso
// avviso "Il Cestino e' vuoto." Solo "Ripristina" e' portato qui -
// "Elimina definitivamente" resta fuori: e' un'operazione distruttiva e
// irreversibile che in V1 cancella anche righe collegate (whatsapp,
// buoni, file PDF) senza equivalenti reali in V2, non improvvisabile in
// sicurezza in questa fase (vedi report finale, gap documentato).
export function PracticeTrashPage() {
  const { data: practices, isLoading, isError } = useTrashedPractices();
  const { data: veterinarians } = useVeterinarians({});
  const restore = useRestorePractice();
  const navigate = useNavigate();
  const [restoreTarget, setRestoreTarget] = useState<Practice | null>(null);

  const vetNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const vet of veterinarians ?? []) map.set(vet.id, vet.short_name || vet.clinic_name || vet.doctor_name || "");
    return map;
  }, [veterinarians]);

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Cestino</h1>
          <div className="sub">Pratiche eliminate ma ancora recuperabili.</div>
        </div>
        <button className="btn-ghost" onClick={() => navigate("/pratiche")}>
          Torna all'elenco
        </button>
      </div>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento del Cestino.</p>}

      {practices && practices.length === 0 && <section className="section empty-state">Il Cestino e' vuoto.</section>}

      {practices && practices.length > 0 && (
        <>
          <div className="trash-note">
            Le pratiche nel Cestino non compaiono in Dashboard e Archivio. Ripristinale se sono state eliminate per
            errore.
          </div>
          <div className="tablebox">
            <table className="premium-table">
              <thead>
                <tr>
                  <th>Data recupero</th>
                  <th>Pratica</th>
                  <th>Animale</th>
                  <th>Speditore</th>
                  <th>Veterinario</th>
                  <th>Azioni</th>
                </tr>
              </thead>
              <tbody>
                {practices.map((practice) => {
                  const firstAnimal = practice.animals[0];
                  const [emoji, avatarCls] = speciesAvatar(firstAnimal?.species ?? null);
                  const owner = [practice.owner_snapshot?.first_name, practice.owner_snapshot?.last_name]
                    .filter(Boolean)
                    .join(" ") || (practice.owner_snapshot?.company_name as string | undefined) || "-";
                  return (
                    <tr key={practice.id}>
                      <td>{new Date(practice.created_at).toLocaleDateString("it-IT")}</td>
                      <td>
                        <div className="premium-row-lead">
                          {/* V1 (trash_page, app.py:18327): l'avatar porta anche la
                              classe specie (avatar-dog/avatar-cat/avatar-other) - e'
                              lei a colorare lo sfondo di .premium-avatar (regola CSS
                              .avatar-* generica, non solo il badge). */}
                          <span className={`premium-avatar ${avatarCls}`} aria-hidden="true">
                            {emoji}
                          </span>
                          <div>
                            <Link to={`/pratiche/${practice.id}`}>
                              <b>{practice.original_practice_number || practice.practice_number}</b>
                            </Link>
                            <br />
                            {/* V1: nessuna classe "sub" qui, e la data e' il valore
                                grezzo di deleted_at con "T" sostituita da uno spazio
                                (nessuna localizzazione it-IT) - stesso identico testo. */}
                            <small>Cestinata il {practice.deleted_at ? practice.deleted_at.replace("T", " ") : "-"}</small>
                          </div>
                        </div>
                      </td>
                      <td>{firstAnimal?.name || "Da inserire"}</td>
                      <td>{owner}</td>
                      <td>{(practice.veterinarian_id && vetNameById.get(practice.veterinarian_id)) || "-"}</td>
                      <td>
                        <div className="actions">
                          <button className="btn-ghost" onClick={() => setRestoreTarget(practice)}>
                            Ripristina
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <ConfirmModal
        open={restoreTarget != null}
        onClose={() => setRestoreTarget(null)}
        onConfirm={() => {
          if (restoreTarget) restore.mutate(restoreTarget.id);
          setRestoreTarget(null);
        }}
        title="Ripristina pratica"
        description={restoreTarget ? `Ripristinare la pratica ${restoreTarget.practice_number}?` : ""}
        confirmLabel="Ripristina"
        isPending={restore.isPending}
      />
    </main>
  );
}
