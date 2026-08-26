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
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <label>
          Nome sede
          <input {...register("name")} />
        </label>
        {errors.name && <p className="field-error">{errors.name.message}</p>}

        <label className="field-row">
          <input type="checkbox" {...register("has_cremation_plant")} />
          Ha impianto di cremazione
        </label>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva sede"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/sedi")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
