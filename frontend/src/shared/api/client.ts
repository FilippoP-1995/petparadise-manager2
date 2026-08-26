import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Client HTTP unico, tipizzato dallo schema OpenAPI generato dal backend
// (doc10 "Contratto API - non duplicare i tipi a mano"). Ogni feature lo
// importa, nessuno scrive fetch() a mano con URL/tipi propri.
// baseUrl vuota: i path dello schema OpenAPI generato includono gia' il
// prefisso /api (definito una sola volta nei router FastAPI), niente da
// aggiungere qui - altrimenti si duplicherebbe in /api/api/....
export const apiClient = createClient<paths>({
  baseUrl: "",
  credentials: "include", // sessione via cookie httpOnly, non un token in localStorage
});
