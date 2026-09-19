import type { CatalogEntry, CatalogResponse } from "./types";

// Same-origin in production behind a proxy; the Vite dev server proxies /api to FastAPI.
const BASE = import.meta.env.VITE_API_URL ?? "";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    throw new Error(
      response.status === 404 ? "not-found" : `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

export const fetchCatalog = () => get<CatalogResponse>("/api/products");
export const fetchProduct = (id: string) => get<CatalogEntry>(`/api/products/${id}`);
