import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { useCreateUrn, useUpdateUrn, useUrn, type UrnCategoryValue } from "./api";

const schema = z.object({
  category: z.enum(["Urna", "Accessorio", "Calco"]),
  name: z.string().min(1, "Obbligatorio"),
  material: z.string().optional(),
  price_euro: z
    .string()
    .min(1, "Obbligatorio")
    .refine((v) => !Number.isNaN(Number(v.replace(",", "."))) && Number(v.replace(",", ".")) >= 0, "Prezzo non valido"),
  quantity: z.number().min(0, "La quantita' non puo' essere negativa"),
  low_stock_threshold: z.number().min(0),
  notes: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

function centsToEuroString(cents: number): string {
  return (cents / 100).toFixed(2);
}

function euroStringToCents(value: string): number {
  return Math.round(Number(value.replace(",", ".")) * 100);
}

export function UrnFormPage() {
  const { urnId } = useParams();
  const [searchParams] = useSearchParams();
  const id = urnId ? Number(urnId) : undefined;
  const isEdit = id !== undefined;
  const navigate = useNavigate();
  const { data: existing } = useUrn(id ?? Number.NaN);
  const createUrn = useCreateUrn();
  const updateUrn = useUpdateUrn();
  const [serverError, setServerError] = useState<string | null>(null);

  const defaultCategory = (searchParams.get("categoria") as UrnCategoryValue) || "Urna";

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { category: defaultCategory, price_euro: "0.00", quantity: 0, low_stock_threshold: 3 },
  });

  useEffect(() => {
    if (existing) {
      reset({
        category: existing.category,
        name: existing.name,
        material: existing.material ?? "",
        price_euro: centsToEuroString(existing.price_cents),
        quantity: existing.quantity,
        low_stock_threshold: existing.low_stock_threshold,
        notes: existing.notes ?? "",
      });
    }
  }, [existing, reset]);

  async function onSubmit(values: FormValues) {
    setServerError(null);
    const input = {
      category: values.category,
      name: values.name,
      material: values.material,
      price_cents: euroStringToCents(values.price_euro),
      quantity: values.quantity,
      low_stock_threshold: values.low_stock_threshold,
      notes: values.notes,
    };
    try {
      if (isEdit) {
        await updateUrn.mutateAsync({ urnId: id, input });
      } else {
        await createUrn.mutateAsync(input);
      }
      navigate("/catalogo-urne");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>{isEdit ? "Modifica articolo" : "Nuovo articolo"}</h1>
      </div>
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <label>
          Categoria
          <select {...register("category")}>
            <option value="Urna">Urna</option>
            <option value="Accessorio">Accessorio</option>
            <option value="Calco">Calco</option>
          </select>
        </label>

        <label>
          Nome
          <input {...register("name")} />
        </label>
        {errors.name && <p className="field-error">{errors.name.message}</p>}

        <label>
          Materiale
          <input {...register("material")} />
        </label>

        <div className="field-row">
          <label>
            Prezzo €
            <input inputMode="decimal" placeholder="120,00" {...register("price_euro")} />
          </label>
          <label>
            Quantita
            <input type="number" step="1" min="0" {...register("quantity", { valueAsNumber: true })} />
          </label>
          <label>
            Soglia scorte basse
            <input type="number" step="1" min="0" {...register("low_stock_threshold", { valueAsNumber: true })} />
          </label>
        </div>
        {(errors.price_euro || errors.quantity) && (
          <p className="field-error">Prezzo e quantita' non possono essere negativi.</p>
        )}

        <label>
          Note
          <textarea {...register("notes")} />
        </label>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva articolo"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/catalogo-urne")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
