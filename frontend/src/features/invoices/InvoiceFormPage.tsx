import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { usePractice, type Practice } from "@/features/practices/api";
import { PracticePicker } from "@/shared/PracticePicker";
import { euroStringToCents, isValidEuroString } from "@/shared/money";

import { useCreateInvoice } from "./api";

const schema = z.object({
  invoice_number: z.string().min(1, "Obbligatorio"),
  invoice_date: z.string().optional(),
  total_euro: z
    .string()
    .min(1, "Obbligatorio")
    .refine((v) => isValidEuroString(v) && euroStringToCents(v) > 0, "Importo non valido"),
  channel: z.enum(["W", "D"]),
});

type FormValues = z.infer<typeof schema>;

export function InvoiceFormPage() {
  const [searchParams] = useSearchParams();
  const prefillPracticeId = Number(searchParams.get("practice_id") || "");
  const { data: prefillPractice } = usePractice(prefillPracticeId);
  const [selectedPractice, setSelectedPractice] = useState<Practice | null>(null);
  const practice = selectedPractice ?? (Number.isFinite(prefillPracticeId) ? prefillPractice ?? null : null);
  const navigate = useNavigate();
  const createInvoice = useCreateInvoice();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { channel: "W" } });

  async function onSubmit(values: FormValues) {
    if (!practice) {
      setServerError("Seleziona una pratica.");
      return;
    }
    setServerError(null);
    try {
      const invoice = await createInvoice.mutateAsync({
        practice_id: practice.id,
        invoice_number: values.invoice_number,
        invoice_date: values.invoice_date || null,
        total_amount_cents: euroStringToCents(values.total_euro),
        channel: values.channel,
      });
      navigate(`/fatture/${invoice.id}`);
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuova fattura</h1>
      </div>
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <div className="field full">
          <label>Pratica</label>
          <PracticePicker selectedPractice={practice} onSelect={setSelectedPractice} />
        </div>

        <label>
          Numero fattura
          <input {...register("invoice_number")} />
        </label>
        {errors.invoice_number && <p className="field-error">{errors.invoice_number.message}</p>}

        <div className="field-row">
          <label>
            Data fattura
            <input type="date" {...register("invoice_date")} />
          </label>
          <label>
            Importo €
            <input inputMode="decimal" placeholder="340,00" {...register("total_euro")} />
          </label>
          <label>
            Canale
            <select {...register("channel")}>
              <option value="W">W</option>
              <option value="D">D</option>
            </select>
          </label>
        </div>
        {errors.total_euro && <p className="field-error">{errors.total_euro.message}</p>}

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting || !practice}>
            {isSubmitting ? "Salvataggio..." : "Salva fattura"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate(-1)}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
