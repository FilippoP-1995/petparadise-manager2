import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { centsToEuroString, euroStringToCents, isValidEuroString } from "@/shared/money";

import { useCreateUrn, useUpdateUrn, useUrn, useUrns, type UrnCategoryValue } from "./api";

// V1 (urn_edit_page, app.py:13877-13900): titolo dinamico per categoria
// ("Nuova urna"/"Modifica urna"/"Nuovo accessorio"/... - mai un generico
// "Nuovo articolo").
const NEW_ITEM_TITLE: Record<UrnCategoryValue, string> = {
  Urna: "Nuova urna",
  Accessorio: "Nuovo accessorio",
  Calco: "Nuovo calco",
};
const EDIT_ITEM_TITLE: Record<UrnCategoryValue, string> = {
  Urna: "Modifica urna",
  Accessorio: "Modifica accessorio",
  Calco: "Modifica calco",
};

const schema = z.object({
  category: z.enum(["Urna", "Accessorio", "Calco"]),
  name: z.string().min(1, "Obbligatorio"),
  material: z.string().optional(),
  price_euro: z
    .string()
    .min(1, "Obbligatorio")
    .refine((v) => isValidEuroString(v) && euroStringToCents(v) >= 0, "Prezzo non valido"),
  quantity: z.number().min(0, "La quantita' non puo' essere negativa"),
  low_stock_threshold: z.number().min(0),
  notes: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

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

  // V1 (urn_edit_page, app.py:13894-13895): <datalist> con i materiali
  // distinti gia' usati, su TUTTE le categorie (nessun filtro category qui,
  // a differenza del filtro Materiale di UrnListPage che e' invece per
  // singola categoria). Nessun endpoint dedicato lato V2: si riusa la
  // stessa lista urne (fino al limite API di 200) per derivare i valori.
  const { data: allUrns } = useUrns({ activeOnly: true, limit: 200 });
  const materialOptions = useMemo(() => {
    if (!allUrns) return [];
    return Array.from(new Set(allUrns.map((u) => u.material).filter((m): m is string => !!m))).sort((a, b) =>
      a.localeCompare(b, "it"),
    );
  }, [allUrns]);

  const defaultCategory = (searchParams.get("categoria") as UrnCategoryValue) || "Urna";

  const {
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { category: defaultCategory, price_euro: "0.00", quantity: 0, low_stock_threshold: 3 },
  });

  const category = watch("category");
  const quantity = watch("quantity");

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
        <div>
          <h1>{isEdit ? EDIT_ITEM_TITLE[category] : NEW_ITEM_TITLE[category]}</h1>
          <p className="sub">I dati saranno disponibili subito nella compilazione delle pratiche.</p>
        </div>
      </div>
      {/* V1 (urn_edit_page, app.py:13899): <form class="section"><div
          class="fields"> - non una <form class="card"> con <label> a
          livello radice. Stesso ordine campi: Categoria/Nome/Materiale/
          Codice interno/Prezzo/Quantita (stepper)/Soglia scorte/Foto/Note. */}
      <form className="section" onSubmit={handleSubmit(onSubmit)}>
        <div className="fields">
          <div className="field">
            <label>
              Categoria
              <select {...register("category")}>
                <option value="Urna">Urna</option>
                <option value="Accessorio">Accessorio</option>
                <option value="Calco">Calco</option>
              </select>
            </label>
          </div>

          <div className="field">
            <label>
              Nome *
              <input {...register("name")} />
            </label>
            {errors.name && <p className="field-error">{errors.name.message}</p>}
          </div>

          <div className="field">
            <label>
              Materiale
              <input placeholder="Scegli o scrivi un materiale" list="urnMaterialOptions" {...register("material")} />
              <datalist id="urnMaterialOptions">
                {materialOptions.map((material) => (
                  <option key={material} value={material} />
                ))}
              </datalist>
            </label>
          </div>

          {/* V1: "Codice interno" - generato automaticamente lato server
              (non un campo di UrnCreate/UrnUpdate nel backend V2, stesso
              identico comportamento: sempre sola lettura qui, non solo
              per i nuovi articoli come in V1). */}
          <div className="field">
            <label>
              Codice interno
              <input value={existing?.internal_code ?? ""} placeholder="Generato automaticamente" readOnly disabled />
            </label>
            <small className="sub">Assegnato automaticamente, non modificabile.</small>
          </div>

          <div className="field">
            <label>
              Prezzo € *
              <input inputMode="decimal" placeholder="120,00" {...register("price_euro")} />
            </label>
          </div>

          <div className="field">
            <label>Quantita disponibile</label>
            <div className="quantity-stepper urn-quantity-stepper">
              <button
                className="btn-ghost"
                type="button"
                aria-label="Diminuisci quantità"
                onClick={() => setValue("quantity", Math.max(0, (quantity || 0) - 1))}
              >
                −
              </button>
              <input type="number" step="1" min="0" inputMode="numeric" {...register("quantity", { valueAsNumber: true })} />
              <button
                className="btn-ghost"
                type="button"
                aria-label="Aumenta quantità"
                onClick={() => setValue("quantity", (quantity || 0) + 1)}
              >
                +
              </button>
            </div>
          </div>

          <div className="field">
            <label>
              Soglia scorte basse
              <input type="number" step="1" min="0" inputMode="numeric" {...register("low_stock_threshold", { valueAsNumber: true })} />
            </label>
          </div>

          {(errors.price_euro || errors.quantity) && (
            <p className="field-error">Prezzo e quantita' non possono essere negativi.</p>
          )}

          {/* V1 (urn_edit_page, app.py:13899): campo "Foto (PNG, JPG o WEBP;
              max 3 MB)" con upload immagine. Il backend V2 (UrnCreate/
              UrnUpdate in schemas/urn.py) non ha alcun campo immagine, quindi
              il campo e' omesso qui invece di fabbricare un upload finto che
              non salverebbe nulla. */}

          <div className="field full">
            <label>
              Note
              <textarea {...register("notes")} />
            </label>
          </div>
        </div>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions" style={{ marginTop: 16 }}>
          <button className="btn" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/catalogo-urne")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
