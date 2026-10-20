// V1 (species_avatar, app.py:7221-7227): stessa emoji, stessa classe
// colore - non un'icona generica. Condiviso tra Lista Pratiche e
// Dashboard (Ultime pratiche), stessa fonte unica di V1.
export function speciesAvatar(species: string | null | undefined): [string, string] {
  const label = (species ?? "").trim().toLowerCase();
  if (label === "cane") return ["🐶", "avatar-dog"];
  if (label === "gatto") return ["🐱", "avatar-cat"];
  return ["🐾", "avatar-other"];
}
