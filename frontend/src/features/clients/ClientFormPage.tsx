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
    email: z.string().email("Email non valida").optional().or(z.literal("")),
    city: z.string().optional(),
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
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuovo cliente</h1>
      </div>
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <div className="field-row">
          <label>
            Nome
            <input {...register("first_name")} />
          </label>
          <label>
            Cognome
            <input {...register("last_name")} />
          </label>
        </div>
        <label>
          Ragione sociale (in alternativa a nome/cognome)
          <input {...register("company_name")} />
        </label>
        {errors.first_name && <p className="field-error">{errors.first_name.message}</p>}
        <div className="field-row">
          <label>
            Telefono
            <input {...register("phone")} />
          </label>
          <label>
            Email
            <input {...register("email")} />
          </label>
        </div>
        {errors.email && <p className="field-error">{errors.email.message}</p>}
        <label>
          Citta'
          <input {...register("city")} />
        </label>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva cliente"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/clienti")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
