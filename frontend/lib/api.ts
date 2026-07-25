import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
  timeout: 15000,
});

/** Instrada un link d'acquisto attraverso /go (tracking + affiliazione + allowlist).
 *  Se url Ã¨ assente ritorna "#". */
export const outbound = (
  url?: string | null,
  chain?: string | null,
  productId?: string | null
): string => {
  if (!url) return "#";
  const p = new URLSearchParams({ u: url });
  if (chain) p.set("chain", chain);
  if (productId) p.set("pid", productId);
  return `${API_BASE}/go?${p.toString()}`;
};

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("access_token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export default api;

// â”€â”€ Tipi â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface Store {
  id: string;
  name: string;
  address: string;
  city: string;
  chain_name: string;
  chain_slug: string;
  has_delivery: boolean;
  has_click_collect: boolean;
  has_online_shop: boolean;
  shop_url: string | null;
  distance_km: number;
}

export interface Product {
  id: string;
  barcode: string | null;
  name: string;
  brand: string | null;
  image_url: string | null;
  unit: string | null;
  unit_quantity: number | null;
  /** Prezzo minimo corrente (entro il raggio se la posizione Ã¨ attiva). */
  min_price?: number | null;
  /** Numero di negozi con un prezzo corrente per questo prodotto. */
  price_store_count?: number | null;
  /** Numero di negozi dove il prodotto risulta disponibile. */
  available_store_count?: number | null;
  /** Catena con il miglior prezzo corrente per il risultato in lista. */
  best_price_chain_name?: string | null;
  best_price_chain_slug?: string | null;
  best_price_store_name?: string | null;
  best_price_in_stock?: boolean | null;
  best_price_scraped_at?: string | null;
  best_price_per_unit?: number | null;
  /** Prezzo minimo per unita (EUR/kg o EUR/L) sui risultati di ricerca. */
  min_price_per_unit?: number | null;
}

export interface PriceLocation {
  store_id: string;
  store_name: string;
  address: string;
  distance_km: number | null;
  in_stock: boolean;
  is_online: boolean;
  price: number;
}

export interface PriceResult {
  price: number;
  original_price: number | null;
  promo_label: string | null;
  price_per_unit: number | null;
  in_stock: boolean;
  scraped_at: string;
  store_id: string;
  store_name: string;
  address: string;
  chain_name: string;
  chain_slug: string;
  shop_url: string | null;
  has_delivery: boolean;
  has_click_collect: boolean;
  /** null per i negozi online (spesa nazionale, distanza non significativa). */
  distance_km: number | null;
  /** true se e un negozio virtuale di spesa online (consegna nazionale). */
  is_online: boolean;
  /** Sedi della stessa catena aggregate nella vista prodotto. */
  chain_locations?: PriceLocation[];
  /** true se il prezzo non viene aggiornato da tempo: da verificare sul sito. */
  stale?: boolean;
}
// â”€â”€ Copertura catene â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface ChainCoverage {
  slug: string;
  name: string;
  products_with_current_price: number;
  physical_stores: number;
  has_online_shop: boolean;
  last_scraped_at: string | null;
  /** "full" = confronto completo, "promo" = solo offerte/volantino, "none" = nessun dato. */
  tier: "full" | "promo" | "none";
}

export const getChainsCoverage = (): Promise<ChainCoverage[]> =>
  api
    .get<{ chains: ChainCoverage[] }>("/stores/coverage")
    .then((r) => r.data.chains || []);

// â”€â”€ API calls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/** Codifica un poligono [[lat,lng],â€¦] come "lat,lng;lat,lng;â€¦" per la query.
 *  Ritorna undefined se l'area non Ã¨ valida (< 3 punti). */
export const encodeArea = (
  area?: [number, number][] | null
): string | undefined => {
  if (!area || area.length < 3) return undefined;
  return area.map(([la, ln]) => `${la.toFixed(6)},${ln.toFixed(6)}`).join(";");
};

export const searchProducts = (
  q: string,
  lat?: number,
  lng?: number,
  radiusKm?: number,
  area?: [number, number][] | null
) =>
  api
    .get<Product[]>("/products/search", {
      params: {
        q,
        limit: 40,
        lat,
        lng,
        radius_km: radiusKm,
        area: encodeArea(area),
      },
    })
    .then((r) => r.data);

export const getProductPrices = (
  productId: string,
  lat: number,
  lng: number,
  radiusKm: number,
  area?: [number, number][] | null
) =>
  api
    .get<PriceResult[]>(`/products/${productId}/prices`, {
      params: { lat, lng, radius_km: radiusKm, area: encodeArea(area) },
    })
    .then((r) => r.data);

export const getNearbyStores = (lat: number, lng: number, radiusKm: number) =>
  api
    .get<Store[]>("/stores/nearby", { params: { lat, lng, radius_km: radiusKm } })
    .then((r) => r.data);

export const scanBarcode = (barcode: string, lat: number, lng: number, radiusKm: number) =>
  api
    .get(`/scan/${barcode}`, { params: { lat, lng, radius_km: radiusKm } })
    .then((r) => r.data);

export const optimizeList = (
  listId: string,
  lat: number,
  lng: number,
  radiusKm: number
) =>
  api
    .post(`/lists/${listId}/optimize`, { lat, lng, radius_km: radiusKm })
    .then((r) => r.data);

// â”€â”€ Ottimizzatore lista "quick" (stateless, senza login) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface QuickStoreItem {
  query: string;
  quantity: number;
  price: number;
  subtotal: number;
  product_name: string;
  product_url: string | null;
  image_url?: string | null;
  /** "exact" = prodotto ancorato dall'utente, "text" = match testuale automatico. */
  match_type?: "exact" | "text";
  matched_product_name?: string;
  matched_product_id?: string;
  price_per_unit?: number | null;
  /** Disponibilità del prodotto in quel negozio (false = risulta esaurito). */
  in_stock?: boolean | null;
}

/** Strategia di ottimizzazione del piano carrello (Fase 1 auto-carrello). */
export type PlanStrategy = "cheapest" | "fewest_stores" | "availability";

/**
 * Invia il piano di un negozio all'estensione browser (Fase 2 auto-carrello),
 * via postMessage sulla stessa origine. Se l'estensione non è installata non
 * accade nulla (il chiamante gestisce il timeout/ack). Nessuna credenziale
 * transita: l'estensione agisce sulla sessione già autenticata dell'utente.
 */
export interface ExtensionCartPlan {
  chain_slug?: string | null;
  chain_name: string;
  items: { product_name: string; product_url: string | null; quantity: number }[];
}
export function sendPlanToExtension(payload: ExtensionCartPlan) {
  if (typeof window === "undefined") return;
  window.postMessage({ source: "spesasmart", type: "CART_PLAN", payload }, window.location.origin);
}

export interface QuickStore {
  store_id: string;
  store_name: string;
  chain_name: string;
  chain_slug: string;
  shop_url: string | null;
  has_delivery: boolean;
  has_click_collect: boolean;
  is_online: boolean;
  distance_km: number | null;
  total: number;
  covered: number;
  items: QuickStoreItem[];
}

export interface QuickOptimizeResult {
  n_items: number;
  n_findable: number;
  /** Strategia applicata dal backend (rimandata per coerenza UI). */
  strategy?: PlanStrategy;
  /** Piano consigliato: "single" (meno negozi) o "multi" (prezzo/disponibilità). */
  recommended_plan?: "single" | "multi";
  /** Quante voci del piano risultano disponibili (in stock). */
  in_stock_count?: number;
  best_single: QuickStore | null;
  single_ranking: QuickStore[];
  multi_store: {
    total: number;
    savings_vs_single: number;
    stores: {
      store_id: string;
      store_name: string;
      chain_name: string;
      chain_slug?: string | null;
      shop_url: string | null;
      has_delivery?: boolean;
      has_click_collect?: boolean;
      subtotal: number;
      items: QuickStoreItem[];
    }[];
  };
  not_found: string[];
}

export const optimizeQuick = (
  items: { query: string; quantity?: number; product_id?: string }[],
  lat: number,
  lng: number,
  radiusKm: number,
  strategy: PlanStrategy = "cheapest"
): Promise<QuickOptimizeResult> =>
  api
    .post<QuickOptimizeResult>("/lists/optimize-quick", {
      items,
      lat,
      lng,
      radius_km: radiusKm,
      strategy,
    })
    .then((r) => r.data);

export interface ReceiptItem {
  name: string;
  quantity: number;
  unit_price: number | null;
  total_price: number | null;
  is_discount: boolean;
  matched_product: Product | null;
}

export interface ReceiptResult {
  store_name: string | null;
  store_address: string | null;
  store_chain: string | null;
  purchase_date: string | null;
  total_amount: number | null;
  items: ReceiptItem[];
  items_count: number;
}

export interface PriceComparison {
  store_count: number;
  price_min: number;
  price_max: number;
  price_avg: number;
  delta_pct: number;
  vs_avg: string;
}

export interface PriceSubmitResult {
  saved: boolean;
  product: { id: string; name: string; barcode: string };
  submitted_price: number;
  comparison: PriceComparison;
}

export const submitPrice = (
  barcode: string,
  storeId: string,
  price: number
): Promise<PriceSubmitResult> =>
  api
    .post<PriceSubmitResult>(`/scan/${barcode}/price`, { store_id: storeId, price })
    .then((r) => r.data);

export const parseReceipt = (file: File): Promise<ReceiptResult> => {
  const form = new FormData();
  form.append("file", file);
  return api
    .post<ReceiptResult>("/receipts/parse", form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};

// â”€â”€ Agente: parsing prompt lato server (LLM) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface AgentParsedItem {
  query: string;
  quantity: number;
}

export interface AgentParseResult {
  items: AgentParsedItem[];
  /** "llm" quando la lista arriva dal modello lato server. */
  source: string;
}

/** Trasforma un prompt libero in item {query, quantity} via LLM lato server.
 *  Risponde 503 {"detail":"llm_unavailable"} se il modello non Ã¨ disponibile:
 *  il chiamante deve avere un fallback locale. */
export const parseAgentPrompt = (prompt: string): Promise<AgentParseResult> =>
  api.post<AgentParseResult>("/agent/parse", { prompt }).then((r) => r.data);

// â”€â”€ Avvisi di prezzo (watch) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface Watch {
  id: string;
  product_id: string;
  email?: string;
  threshold_price?: number | null;
  product_name?: string | null;
  created_at?: string | null;
}

export const createWatch = (
  productId: string,
  email: string,
  thresholdPrice?: number | null
): Promise<Watch> =>
  api
    .post<Watch>("/watches", {
      product_id: productId,
      email,
      ...(thresholdPrice != null ? { threshold_price: thresholdPrice } : {}),
    })
    .then((r) => r.data);

export const getWatches = (email: string): Promise<Watch[]> =>
  api
    .get<Watch[] | { watches: Watch[] }>("/watches", { params: { email } })
    .then((r) => (Array.isArray(r.data) ? r.data : r.data?.watches || []));

export const deleteWatch = (id: string, email: string) =>
  api.delete(`/watches/${id}`, { params: { email } }).then((r) => r.data);

// â”€â”€ Spesa abituale (liste ricorrenti con digest email) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export interface RecurringItemInput {
  query: string;
  quantity: number;
  product_id?: string;
}

export interface RecurringItem {
  id: string;
  query: string;
  quantity: number;
  product_id: string | null;
  product_name_resolved?: string | null;
  image_url?: string | null;
}

export interface RecurringList {
  id: string;
  name: string;
  last_digest_at?: string | null;
  items: RecurringItem[];
}

export const createRecurringList = (
  email: string,
  name: string,
  items: RecurringItemInput[]
): Promise<RecurringList> =>
  api
    .post<RecurringList>("/recurring", { email, name, items })
    .then((r) => r.data);

export const getRecurringLists = (email: string): Promise<RecurringList[]> =>
  api
    .get<{ lists: RecurringList[] }>("/recurring", { params: { email } })
    .then((r) => r.data.lists || []);

export const updateRecurringList = (
  id: string,
  email: string,
  name: string,
  items: RecurringItemInput[]
): Promise<RecurringList> =>
  api
    .put<RecurringList>(`/recurring/${id}`, { email, name, items })
    .then((r) => r.data);

export const deleteRecurringList = (id: string, email: string) =>
  api.delete(`/recurring/${id}`, { params: { email } }).then((r) => r.data);

// â”€â”€ Verifica offerte (promo check) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export type PromoVerdict =
  | "true_promo"
  | "weak_promo"
  | "fake_promo"
  | "insufficient_history";

export interface PromoCheckRow {
  store_id: string;
  chain_name: string;
  current_price: number;
  median_60d: number | null;
  discount_pct: number | null;
  verdict: PromoVerdict;
}

export interface PromoCheckResult {
  product_id: string;
  checks: PromoCheckRow[];
}

export const getPromoCheck = (productId: string): Promise<PromoCheckResult> =>
  api.get<PromoCheckResult>(`/promo/${productId}`).then((r) => r.data);

