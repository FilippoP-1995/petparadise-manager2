import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { useCompanyLocation, useCreateCompanyLocation, useUpdateCompanyLocation } from "./api";

const schema = z.object({
  name: z.string().min(1, "Obbligatorio"),
  has_cremation_plant: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

export function CompanyLocationFormPage() {
  const { locationId } = useParams();
  const id = locationId ? Number(locationId) : undefined;
  const isEdit = id !== undefined;
  const navigate = useNavigate();
  const { data: existing } = useCompanyLocation(id ?? Number.NaN);
  const createLocation = useCreateCompanyLocation();
  const updateLocation = useUpdateCompanyLocation();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { has_cremation_plant: false } });

  useEffect(() => {
    if (existing) reset({ name: existing.name, has_cremation_plant: existing.has_cremation_plant });
  }, [existing, reset]);

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      if (isEdit) {
        await updateLocation.mutateAsync({ locationId: id, input: values });
      } else {
        await createLocation.mutateAsync(values);
      }
      navigate("/sedi");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>{isEdit ? "Modifica sede" : "Nuova sede"}</h1>
      </div>
      {/* V1 ha una vera controparte per "Sedi": route_locations_page /
          save_route_location, app.py:11710-11754, servita su
          "/percorso-giornaliero/sedi" (non un item di menu proprio - vive
          sotto Percorso giornaliero, da cui il restructuring V2 gia'
          disclosed in AppShell.tsx/MENU_CARD_META). Li' ogni riga e' un
          <form class="tablebox"><div class="fields"><div class="field">
          <label>Nome sede</label>... - stesso pattern .section/.fields/
          .field riusato qui. Due differenze reali, non inventate:
          1) V1 ha anche indirizzo/lat/lng con ricerca Nominatim
             (routeLocationsInitAddressLookups) - deliberatamente assenti
             nel modello V2 (models/company_location.py: quei campi
             esistono in V1 solo per il Percorso Giornaliero, dominio non
             ancora costruito in V2, non anticipato qui).
          2) "Ha impianto di cremazione" e' un campo reale del backend V2
             (has_cremation_plant) senza equivalente in V1 - nessuna sede
             V1 lo traccia in UI.
          Il checkbox usa .modern-check (V1 app.py:1692, checkbox-riga
          "chiuso" degli orari veterinario) invece di ".day-checkbox", che
          non esiste in app.py (gia' segnalata come classe inventata in
          VeterinarianFormPage.tsx per lo stesso motivo). */}
      <div className="section">
        {/* Heading: "Aggiungi sede" e' il testo esatto di V1 (app.py:11727)
            per la creazione. V1 non ha un heading di modifica dedicato (la
            riga esistente e' modificabile inline, senza titolo proprio) -
            "Modifica sede" resta il testo piu' fedele disponibile per la
            pagina di edit separata di V2. */}
        <h2>{isEdit ? "Modifica sede" : "Aggiungi sede"}</h2>
        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="fields">
            <div className="field">
              <label>
                Nome sede
                <input {...register("name")} />
              </label>
              {errors.name && <p className="field-error">{errors.name.message}</p>}
            </div>
            <div className="field">
              <label className="modern-check">
                <input type="checkbox" {...register("has_cremation_plant")} />
                Ha impianto di cremazione
              </label>
            </div>
          </div>

          {serverError && <p className="error-banner">{serverError}</p>}

          {/* Bottone: V1 usa "btn" primario + "Aggiungi sede" per la
              creazione (app.py:11727) e "btn ghost" + "Salva" per la riga
              esistente (app.py:11723) - stessa distinzione qui. */}
          <div className="actions" style={{ marginTop: 12 }}>
            <button className={isEdit ? "btn-ghost" : "btn"} type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Salvataggio..." : isEdit ? "Salva" : "Aggiungi sede"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => navigate("/sedi")}>
              Annulla
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
