import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useCreateClient } from "./api";

// Validazione immediata/UX lato client (doc10): mai sostitutiva della
// validazione server-side reale (domain/client/rules.py) - solo un
// feedback piu' rapido, la regola vera resta nel backend.
const schema = z
  .object({
    first_name: z.string().optional(),
    last_name: z.string().optional(),
    company_name: z.string().optional(),
    phone: z.string().optional(),
    phone_2: z.string().optional(),
    email: z.string().email("Email non valida").optional().or(z.literal("")),
    tax_code: z.string().optional(),
    vat_number: z.string().optional(),
    street: z.string().optional(),
    city: z.string().optional(),
    province: z.string().optional(),
    zip: z.string().optional(),
    notes: z.string().optional(),
  })
  .refine((data) => (data.first_name && data.last_name) || data.company_name, {
    message: "Serve nome e cognome, oppure una ragione sociale.",
    path: ["first_name"],
  });

type FormValues = z.infer<typeof schema>;

export function ClientFormPage() {
  const navigate = useNavigate();
  const createClient = useCreateClient();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      await createClient.mutateAsync(values);
      navigate("/clienti");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    // V1 (clients_page, app.py:15407): <section class="section"><h2>
    // Aggiungi cliente</h2><form><div class="fields"> - stessi 13 campi in
    // un'unica griglia 2 colonne, non <form class="card"> con "field-row"
    // manuali (classe che non esiste in V1, rimossa).
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuovo cliente</h1>
      </div>
      <div className="section">
        <h2>Aggiungi cliente</h2>
        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="fields">
            <div className="field">
              <label>
                Nome
                <input {...register("first_name")} />
              </label>
            </div>
            <div className="field">
              <label>
                Cognome
                <input {...register("last_name")} />
              </label>
            </div>
            <div className="field">
              <label>
                Ragione sociale
                <input {...register("company_name")} />
              </label>
            </div>
            {errors.first_name && <p className="field-error">{errors.first_name.message}</p>}
            <div className="field">
              <label>
                Telefono
                <input {...register("phone")} />
              </label>
            </div>
            <div className="field">
              <label>
                Secondo telefono
                <input {...register("phone_2")} />
              </label>
            </div>
            <div className="field">
              <label>
                Email
                <input {...register("email")} />
              </label>
            </div>
            {errors.email && <p className="field-error">{errors.email.message}</p>}
            <div className="field">
              <label>
                Codice fiscale
                <input {...register("tax_code")} />
              </label>
            </div>
            <div className="field">
              <label>
                Partita IVA
                <input {...register("vat_number")} />
              </label>
            </div>
            <div className="field full">
              <label>
                Indirizzo
                <input {...register("street")} />
              </label>
            </div>
            <div className="field">
              <label>
                Comune
                <input {...register("city")} />
              </label>
            </div>
            <div className="field">
              <label>
                Provincia
                <input maxLength={2} {...register("province")} />
              </label>
            </div>
            <div className="field">
              <label>
                CAP
                <input {...register("zip")} />
              </label>
            </div>
            <div className="field full">
              <label>
                Note
                <input {...register("notes")} />
              </label>
            </div>
          </div>

          {serverError && <p className="error-banner">{serverError}</p>}

          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn" type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Salvataggio..." : "Salva cliente"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => navigate("/clienti")}>
              Annulla
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
