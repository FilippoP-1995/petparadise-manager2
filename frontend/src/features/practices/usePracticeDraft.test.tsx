import { act, render, renderHook } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePracticeDraft } from "./usePracticeDraft";

/** Riproduce lo stesso pattern di PracticeFormPage: un autosave effect
 * guardato da hasStoredDraft, per verificare che un mount con una bozza
 * gia' presente non la sovrascriva mai con i valori di default prima che
 * l'utente possa scegliere di ripristinarla. */
function FormLikeConsumer({ blankValues }: { blankValues: { name: string } }) {
  const draft = usePracticeDraft<{ name: string }>("test-key");
  useEffect(() => {
    if (draft.hasStoredDraft) return;
    draft.save(blankValues);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.hasStoredDraft]);
  return null;
}

describe("usePracticeDraft", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("non segnala una bozza quando lo storage e' vuoto", () => {
    const { result } = renderHook(() => usePracticeDraft("test-key"));
    expect(result.current.hasStoredDraft).toBe(false);
  });

  it("salva in localStorage dopo il debounce, non ad ogni chiamata", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => usePracticeDraft<{ name: string }>("test-key"));

    act(() => result.current.save({ name: "bozza1" }));
    expect(localStorage.getItem("ppm:draft:test-key")).toBeNull();

    act(() => vi.advanceTimersByTime(500));
    expect(JSON.parse(localStorage.getItem("ppm:draft:test-key")!)).toEqual({ name: "bozza1" });
  });

  it("una bozza salvata sopravvive a un nuovo mount (simula refresh)", () => {
    localStorage.setItem("ppm:draft:test-key", JSON.stringify({ name: "sopravvissuta" }));

    const { result } = renderHook(() => usePracticeDraft<{ name: string }>("test-key"));
    expect(result.current.hasStoredDraft).toBe(true);
    expect(result.current.readDraft()).toEqual({ name: "sopravvissuta" });
  });

  it("regressione: un autosave guardato da hasStoredDraft non sovrascrive una bozza esistente al mount", () => {
    vi.useFakeTimers();
    localStorage.setItem("ppm:draft:test-key", JSON.stringify({ name: "bozza-reale-da-non-perdere" }));

    render(<FormLikeConsumer blankValues={{ name: "" }} />);
    act(() => vi.advanceTimersByTime(1000));

    expect(JSON.parse(localStorage.getItem("ppm:draft:test-key")!)).toEqual({ name: "bozza-reale-da-non-perdere" });
  });

  it("clearDraft rimuove la bozza e resetta hasStoredDraft", () => {
    localStorage.setItem("ppm:draft:test-key", JSON.stringify({ name: "x" }));
    const { result } = renderHook(() => usePracticeDraft<{ name: string }>("test-key"));
    expect(result.current.hasStoredDraft).toBe(true);

    act(() => result.current.clearDraft());
    expect(result.current.hasStoredDraft).toBe(false);
    expect(localStorage.getItem("ppm:draft:test-key")).toBeNull();
  });
});
