import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

/** Il backend gira su Render free tier: dopo ~15 min di inattivita' va in
 *  sospensione e il primo risveglio richiede 30-60 s. Con un timeout basso la
 *  prima ricerca falliva e l'utente vedeva un errore al posto dei prodotti. */
export const COLD_START_HINT_MS = 6000;

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
  timeout: 75000,
});

/** Instrada un link d'acquisto attraverso /go (tracking + affiliazione + allowlist).
 *  Se url è assente ritorna "#". */
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

// ── Tipi ────────────────────────────────────────────────────────────────────

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
  /** Prezzo minimo corrente (entro il raggio se la posizione è attiva). */
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
  /** Provenienza del miglior prezzo: "online" | "negozio" | "volantino" |
   *  "scaduta" | "segnalato" | "sconosciuta". Opzionale: i backend precedenti
   *  non la inviano, e in quel caso la UI non deve mostrare nulla (nessun
   *  default inventato).
   *  Da leggere con parseOrigine() in lib/priceOrigin, mai a mano. */
  best_price_origine?: string | null;
  /** Testo italiano pronto da mostrare (es. "offerta volantino, fino al
   *  31/08"). Opzionale come best_price_origine. */
  best_price_origine_label?: string | null;
  /** Prezzo minimo per unita (EUR/kg o EUR/L) sui risultati di ricerca. */
  min_price_per_unit?: number | null;
  /** Provenienza della RIGA da cui nasce `min_price` (LATERAL `minrow` in
   *  products.py), non di best_price: sono due record diversi, perche'
   *  `best` ordina per in_stock DESC (in Postgres NULLS FIRST) mentre
   *  min_price filtra `in_stock IS TRUE`. Chi stampa `min_price` deve usare
   *  QUESTA etichetta: best_price_origine appartiene a un altro prezzo, e
   *  un'etichetta che non appartiene al numero stampato e' peggio di
   *  un'etichetta assente.
   *  Da leggere con parseOrigine(), mai a mano. */
  min_price_origine?: string | null;
  /** Testo italiano pronto per `min_price`. Opzionale come min_price_origine:
   *  se il backend non lo manda, nessuna etichetta. */
  min_price_origine_label?: string | null;
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
  /** Provenienza del prezzo: "online" (listino e-commerce), "negozio"
   *  (listino di scaffale), "volantino" (promo a tempo ancora valida),
   *  "scaduta" (promo finita), "segnalato" (prezzo riferito da un utente) o
   *  "sconosciuta". Opzionale: i backend precedenti non la inviano.
   *  Qui NON arriva `source` grezzo di proposito: l'etichetta italiana ha una
   *  sola fonte di verita', il backend (vedi app/core/price_origin.py).
   *  Da leggere con parseOrigine() in lib/priceOrigin, mai a mano. */
  origine?:
    | "online"
    | "negozio"
    | "volantino"
    | "scaduta"
    | "segnalato"
    | "sconosciuta"
    | null;
  /** Etichetta pronta da mostrare, con la scadenza quando c'e'
   *  (es. "offerta volantino, fino al 15/11"). Opzionale come origine. */
  origine_label?: string | null;
  /** Permesso di affermare uno SCONTO su questa riga: barrato, "-X%", pill
   *  del promo_label, badge "Risparmi". E' una domanda DIVERSA da `origine`
   *  (che dice il tipo di prezzo), e i due assi non vanno fusi: una riga del
   *  catalogo online puo' portare uno sconto dichiarato dalla catena.
   *  Da leggere con scontoAffermabile() in lib/priceOrigin, mai a mano.
   *  Opzionale: senza il campo nessuna affermazione di sconto. */
  sconto_affermabile?: boolean | null;
  /** Nome precedente di `sconto_affermabile`, con la regola piu' stretta del
   *  giro scorso (solo origine "volantino"). Lo manda un backend non ancora
   *  aggiornato; scontoAffermabile() lo legge come ripiego prudente. */
  promozione_viva?: boolean | null;
  /** Avviso di confronto misto, calcolato dal backend e RIPETUTO IDENTICO su
   *  ogni riga della risposta (fino a 30).
   *
   *  Dichiarato perche' esiste sul filo: un campo che arriva e non e' nel tipo
   *  diventa invisibile a chi legge questo file. NON viene reso dalla home,
   *  di proposito: il backend lo calcola su TUTTE le righe della risposta,
   *  mentre la home mostra una riga per catena (groupPricesByChain), quindi
   *  quel testo puo' nominare una natura di prezzo che in quel confronto non
   *  si vede. La home mostra la frase generica (AVVISO_CONFRONTO_MISTO in
   *  lib/priceOrigin, copia del fallback del backend), che non nomina nessuna
   *  categoria e resta vera in ogni combinazione. Dove invece l'insieme reso
   *  coincide con quello calcolato (scheda prodotto, scanner, piano di
   *  acquisto) si passa il testo del backend.
   *
   *  Chiesto al backend di mandarlo UNA volta sola, non su ogni riga: vedi
   *  richieste_ad_altri del giro. */
  avviso?: string | null;
}
// ── Copertura catene ─────────────────────────────────────────────────────────

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

// ── API calls ────────────────────────────────────────────────────────────────

/** Codifica un poligono [[lat,lng],…] come "lat,lng;lat,lng;…" per la query.
 *  Ritorna undefined se l'area non è valida (< 3 punti). */
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

// ── Ottimizzatore lista "quick" (stateless, senza login) ─────────────────────

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
  /** Provenienza di QUESTO prezzo (vedi PriceResult.origine). Il piano
   *  carrello e' il totale di cassa dell'app: una voce scelta da un volantino
   *  vale solo andando in quel negozio, e va dichiarato voce per voce.
   *  Opzionale: i backend precedenti non la inviano. */
  origine?: string | null;
  /** Etichetta italiana pronta, con la scadenza quando c'e'. */
  origine_label?: string | null;
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
  /** Avviso del backend quando il TOTALE di questo negozio somma prezzi di
   *  natura diversa. Opzionale: i backend precedenti non lo inviano. */
  avviso?: string | null;
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
      /** Come QuickStore.avviso, per il singolo negozio dello split. */
      avviso?: string | null;
    }[];
  };
  not_found: string[];
  /** Avviso sul confronto fra i totali (tutto da un negozio contro lo split):
   *  `savings_vs_single` e' una differenza fra due somme che possono avere
   *  natura diversa. Il backend lo manda solo quando il confronto e'
   *  davvero misto: la UI lo rende senza ricalcolare la condizione. */
  avviso?: string | null;
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
  /** Presente solo se la chiamata passava l'email: la spesa e' stata salvata
   *  nello storico (vedi parseReceiptFor). I backend vecchi non lo inviano. */
  purchase_id?: string | null;
}

export interface PriceComparison {
  store_count: number;
  price_min: number;
  price_max: number;
  price_avg: number;
  delta_pct: number;
  vs_avg: string;
  /** Avviso del backend quando min/media/max (e quindi la frase `vs_avg`)
   *  nascono da prezzi di natura diversa: una media fra un listino online e
   *  un'offerta volantino non e' una grandezza che significhi qualcosa, e
   *  l'utente la sta leggendo in corsia. Opzionale: i backend precedenti non
   *  lo inviano. */
  avviso?: string | null;
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

// ── Agente: parsing prompt lato server (LLM) ─────────────────────────────────

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
 *  Risponde 503 {"detail":"llm_unavailable"} se il modello non è disponibile:
 *  il chiamante deve avere un fallback locale. */
export const parseAgentPrompt = (prompt: string): Promise<AgentParseResult> =>
  api.post<AgentParseResult>("/agent/parse", { prompt }).then((r) => r.data);

// ── Avvisi di prezzo (watch) ─────────────────────────────────────────────────

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

// ── Spesa abituale (liste ricorrenti con digest email) ───────────────────────

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

// ── Verifica offerte (promo check) ───────────────────────────────────────────

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
  /** Provenienza del prezzo corrente (vedi PriceResult.origine). */
  origine?: string | null;
  /** Etichetta italiana pronta da mostrare. */
  origine_label?: string | null;
  /** Permesso di affermare uno sconto (vedi PriceResult.sconto_affermabile).
   *  Qui e' anche il presidio che decide SE la riga si rende: vedi
   *  components/ui/PromoCheck.tsx. */
  sconto_affermabile?: boolean | null;
  /** Nome precedente del campo (vedi PriceResult.promozione_viva). */
  promozione_viva?: boolean | null;
  /** true quando la mediana 60gg nasce da sorgenti di natura diversa: il
   *  denominatore dello sconto non e' omogeneo, quindi il verdetto non e'
   *  verificato e non va presentato come una certificazione. */
  mediana_origini_miste?: boolean | null;
}

export interface PromoCheckResult {
  product_id: string;
  checks: PromoCheckRow[];
}

export const getPromoCheck = (productId: string): Promise<PromoCheckResult> =>
  api.get<PromoCheckResult>(`/promo/${productId}`).then((r) => r.data);

// -- Offerte vicino a te -------------------------------------------------------

export interface NearbyOffer {
  product_id: string;
  product_name: string;
  brand: string | null;
  image_url: string | null;
  chain_slug: string;
  chain_name: string;
  store_name: string;
  /** null per gli store online (spesa nazionale, distanza non significativa). */
  distance_km: number | null;
  price: number;
  original_price: number | null;
  /** Sconto % calcolato dal prezzo barrato; null se non dichiarato. */
  discount_pct: number | null;
  promo_label: string | null;
  /** Data di fine promo (ISO "YYYY-MM-DD"), quando nota (es. volantini). */
  promo_expires: string | null;
  /** Nome tecnico dello spider che ha rilevato il prezzo (es. "flyer", "md",
   *  "esselunga_web"). NON interpretarlo nei componenti: la provenienza la
   *  dichiara il backend in `origine`, e classificarla di nuovo lato client
   *  e' il bug per cui md/lidl/aldi/penny non venivano riconosciuti come
   *  volantini. Resta esposto solo come specchio del parametro server
   *  `source=flyer` per il filtro di /offerte su backend vecchi. */
  source: string;
  /** Provenienza del prezzo dichiarata dal backend (vedi
   *  PriceResult.origine). Opzionale: senza di essa nessuna etichetta. */
  origine?: string | null;
  /** Etichetta italiana pronta da mostrare. Opzionale come origine. */
  origine_label?: string | null;
  /** Permesso di affermare uno sconto (vedi PriceResult.sconto_affermabile).
   *  Da leggere con scontoAffermabile() in lib/priceOrigin. */
  sconto_affermabile?: boolean | null;
  /** Nome precedente del campo (vedi PriceResult.promozione_viva). */
  promozione_viva?: boolean | null;
  price_per_unit: number | null;
  /** Posizione dell'offerta dentro la sua catena (1 = la migliore).
   *  Opzionale: i backend precedenti al ranking round-robin non lo inviano. */
  chain_rank?: number;
}

/** Fonte delle offerte: solo volantino oppure tutte. */
export type OffersSource = "flyer" | "all";

/** Migliori promozioni correnti nei negozi vicini + spesa online nazionale.
 *  Una sola offerta (la migliore) per coppia prodotto/catena, con le catene
 *  alternate a giro (round-robin) cosi' che i discount non restino esclusi.
 *
 *  `source="flyer"` chiede le sole promo da volantino. Un backend vecchio
 *  ignora il parametro e risponde con tutto: chi chiama deve quindi filtrare
 *  anche lato client (vedi /offerte). */
export const getNearbyOffers = (
  lat: number,
  lng: number,
  radiusKm: number,
  chain?: string | null,
  source?: OffersSource | null
): Promise<NearbyOffer[]> =>
  api
    .get<NearbyOffer[]>("/offers/nearby", {
      params: {
        lat,
        lng,
        radius_km: radiusKm,
        ...(chain ? { chain } : {}),
        ...(source && source !== "all" ? { source } : {}),
      },
      // La query aggregata sulle promo puo' essere lenta a cache DB fredda.
      timeout: 30000,
    })
    .then((r) => r.data);


// -- Errori API: lettura difensiva di status/detail ---------------------------

export interface ApiErrorInfo {
  /** HTTP status, null se la richiesta non e' mai arrivata a destinazione. */
  status: number | null;
  /** Campo "detail" di FastAPI quando e' una stringa (es. "assistant_unavailable"). */
  detail: string | null;
  /** true su timeout/annullamento: il backend Render potrebbe essere in risveglio. */
  timeout: boolean;
  /** true quando non c'e' stata risposta (rete assente, CORS, server giu'). */
  network: boolean;
}

/** Normalizza un errore axios. Non lancia: usabile dentro i catch. */
export function apiError(err: unknown): ApiErrorInfo {
  if (axios.isAxiosError(err)) {
    const body = err.response?.data as { detail?: unknown } | undefined;
    const rawDetail = body && typeof body === "object" ? body.detail : undefined;
    return {
      status: err.response?.status ?? null,
      detail: typeof rawDetail === "string" ? rawDetail : null,
      timeout: err.code === "ECONNABORTED" || err.code === "ETIMEDOUT",
      network: !err.response,
    };
  }
  return { status: null, detail: null, timeout: false, network: false };
}

/** Frontend (Vercel) e backend (Render) si deployano separatamente: una rotta
 *  nuova puo' ancora non esistere. 404/405/501 = "backend vecchio", non un
 *  errore dell'utente. */
export function isMissingEndpoint(info: ApiErrorInfo): boolean {
  return info.status === 404 || info.status === 405 || info.status === 501;
}

// -- Assistente AI (/assistant/chat) -----------------------------------------

/** Il loop di tool use lato server e' lento: serve piu' dei 75 s del client. */
export const ASSISTANT_TIMEOUT_MS = 90000;

export interface AssistantMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantAction {
  tool?: string | null;
  summary?: string | null;
  ok?: boolean | null;
}

export type RebuildMatchKind =
  | "same_product_on_offer"
  | "same_brand_on_offer"
  | "similar_on_offer"
  | "no_offer_same_product"
  | "not_found";

export interface RebuildOriginal {
  product_id?: string | null;
  name?: string | null;
  brand?: string | null;
  price?: number | null;
  /** Provenienza del prezzo di partenza: `saving_vs_original` e' la
   *  differenza fra questo e il prezzo scelto, e se le due nature non
   *  coincidono quel risparmio non e' confrontabile. Opzionale anche sui
   *  backend aggiornati: sulle voci `not_found` il campo non c'e'. */
  origine?: string | null;
  origine_label?: string | null;
}

export interface RebuildChosen {
  product_id?: string | null;
  name?: string | null;
  brand?: string | null;
  image_url?: string | null;
  price?: number | null;
  original_price?: number | null;
  discount_pct?: number | null;
  price_per_unit?: number | null;
  promo_label?: string | null;
  promo_expires?: string | null;
  /** Provenienza del prezzo scelto (vedi PriceResult.origine). Il selettore
   *  ordina per sconto, quindi le sostituzioni sono quasi tutte da volantino:
   *  senza etichetta il totale sembra un prezzo da pagare online. */
  origine?: string | null;
  origine_label?: string | null;
  /** Permesso di affermare uno sconto (vedi PriceResult.sconto_affermabile):
   *  qui gate di barrato, "-X%", pill del promo_label e del badge "fino al". */
  sconto_affermabile?: boolean | null;
  /** Nome precedente del campo (vedi PriceResult.promozione_viva). */
  promozione_viva?: boolean | null;
  chain_slug?: string | null;
  chain_name?: string | null;
  store_id?: string | null;
  store_name?: string | null;
  distance_km?: number | null;
}

export interface RebuildItem {
  query_name?: string | null;
  original?: RebuildOriginal | null;
  chosen?: RebuildChosen | null;
  /** string oltre all'union: un backend piu' nuovo puo' aggiungere casi. */
  match_kind?: RebuildMatchKind | string | null;
  saving_vs_original?: number | null;
  brand_kept?: boolean | null;
  promo_verdict?: PromoVerdict | string | null;
  note?: string | null;
}

export interface RebuildSummary {
  items_total?: number | null;
  on_offer_count?: number | null;
  brand_kept_count?: number | null;
  brand_changed_count?: number | null;
  not_found_count?: number | null;
  total_estimated?: number | null;
  total_without_offers?: number | null;
  estimated_saving?: number | null;
  /** Avviso del backend quando le voci scelte hanno natura diversa:
   *  `total_estimated` non e' un importo che l'utente possa pagare in una
   *  volta. Opzionale: i backend precedenti non lo inviano. */
  avviso?: string | null;
}

export interface RebuildResultData {
  items?: RebuildItem[] | null;
  summary?: RebuildSummary | null;
}

export interface AssistantNeeds {
  /** string oltre all'union: casi nuovi non devono rompere la UI. */
  type?: "email" | "location" | "no_purchases" | string | null;
  message?: string | null;
}

export interface AssistantChatResponse {
  reply?: string | null;
  actions?: AssistantAction[] | null;
  plan?: QuickOptimizeResult | null;
  rebuild?: RebuildResultData | null;
  needs?: AssistantNeeds | null;
}

export interface AssistantChatInput {
  message: string;
  email?: string | null;
  lat?: number | null;
  lng?: number | null;
  radius_km?: number | null;
  history?: AssistantMessage[] | null;
}

/** Chat con l'assistente. Puo' rispondere 503 {"detail":"assistant_unavailable"}
 *  quando la chiave AI non e' configurata, e 429 su rate limit: chi chiama DEVE
 *  avere un percorso alternativo senza LLM (vedi AssistantBox). */
export const assistantChat = (
  input: AssistantChatInput
): Promise<AssistantChatResponse> =>
  api
    .post<AssistantChatResponse>(
      "/assistant/chat",
      {
        message: input.message,
        ...(input.email ? { email: input.email } : {}),
        ...(input.lat != null ? { lat: input.lat } : {}),
        ...(input.lng != null ? { lng: input.lng } : {}),
        ...(input.radius_km != null ? { radius_km: input.radius_km } : {}),
        ...(input.history && input.history.length ? { history: input.history } : {}),
      },
      { timeout: ASSISTANT_TIMEOUT_MS }
    )
    .then((r) => r.data || {});

// -- Storico spese (/purchases) ----------------------------------------------

export interface PurchaseSummary {
  id: string;
  chain_slug?: string | null;
  chain_name?: string | null;
  purchase_date?: string | null;
  total?: number | null;
  source?: string | null;
  created_at?: string | null;
  items_count?: number | null;
}

export interface PurchaseItemRow {
  id?: string | null;
  name?: string | null;
  brand?: string | null;
  quantity?: number | null;
  unit_price?: number | null;
  product_id?: string | null;
}

export interface PurchaseDetail extends PurchaseSummary {
  items?: PurchaseItemRow[] | null;
}

export interface PurchaseItemInput {
  name: string;
  brand?: string;
  quantity?: number;
  unit_price?: number;
  product_id?: string;
}

export interface CreatePurchaseInput {
  email: string;
  chain_slug?: string | null;
  purchase_date?: string | null;
  total?: number | null;
  source?: string | null;
  items: PurchaseItemInput[];
}

export const getPurchases = (email: string, limit = 20): Promise<PurchaseSummary[]> =>
  api
    .get<{ purchases?: PurchaseSummary[] } | PurchaseSummary[]>("/purchases", {
      params: { email, limit },
    })
    .then((r) => (Array.isArray(r.data) ? r.data : r.data?.purchases || []));

/** Ultima spesa salvata. 404 {"detail":"no_purchases"} se lo storico e' vuoto. */
export const getLastPurchase = (email: string): Promise<PurchaseDetail> =>
  api.get<PurchaseDetail>("/purchases/last", { params: { email } }).then((r) => r.data);

/** Dettaglio di una spesa specifica. NON fa parte del contratto minimo del
 *  backend: chi chiama deve gestire isMissingEndpoint() e ripiegare su
 *  /purchases/last quando la spesa richiesta e' la piu' recente. */
export const getPurchase = (id: string, email: string): Promise<PurchaseDetail> =>
  api
    .get<PurchaseDetail>(`/purchases/${encodeURIComponent(id)}`, { params: { email } })
    .then((r) => r.data);

export const createPurchase = (
  input: CreatePurchaseInput
): Promise<{ id?: string; items_count?: number }> =>
  api
    .post<{ id?: string; items_count?: number }>("/purchases", {
      email: input.email,
      ...(input.chain_slug ? { chain_slug: input.chain_slug } : {}),
      ...(input.purchase_date ? { purchase_date: input.purchase_date } : {}),
      ...(input.total != null ? { total: input.total } : {}),
      ...(input.source ? { source: input.source } : {}),
      items: input.items,
    })
    .then((r) => r.data || {});

/** Scontrino -> articoli. Con `email` il backend salva anche la spesa e
 *  rimanda `purchase_id`; senza email estrae soltanto i dati.
 *  parseReceipt() resta invariata per lo scanner. */
export const parseReceiptFor = (
  file: File,
  email?: string | null
): Promise<ReceiptResult> => {
  const form = new FormData();
  form.append("file", file);
  if (email) form.append("email", email);
  return api
    .post<ReceiptResult>("/receipts/parse", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: ASSISTANT_TIMEOUT_MS,
    })
    .then((r) => r.data);
};

// -- Rifai la spesa con le offerte (/rebuild/from-items) ---------------------

export const rebuildFromItems = (
  items: PurchaseItemInput[],
  lat: number,
  lng: number,
  radiusKm?: number | null,
  opts?: { chain_slug?: string | null; keep_brand?: boolean }
): Promise<RebuildResultData> =>
  api
    .post<RebuildResultData>(
      "/rebuild/from-items",
      {
        items,
        lat,
        lng,
        ...(radiusKm != null ? { radius_km: radiusKm } : {}),
        ...(opts?.chain_slug ? { chain_slug: opts.chain_slug } : {}),
        ...(opts?.keep_brand != null ? { keep_brand: opts.keep_brand } : {}),
      },
      { timeout: ASSISTANT_TIMEOUT_MS }
    )
    .then((r) => r.data || {});

/** Normalizza gli articoli di una spesa nel formato di /rebuild/from-items,
 *  scartando le righe senza nome utile (sconti, totali, resi). */
export function purchaseItemsToRebuildItems(
  items?: PurchaseItemRow[] | null
): PurchaseItemInput[] {
  if (!Array.isArray(items)) return [];
  const out: PurchaseItemInput[] = [];
  for (const it of items) {
    const name = (it?.name || "").trim();
    if (name.length < 2) continue;
    const row: PurchaseItemInput = { name };
    if (it.brand) row.brand = it.brand;
    if (it.quantity != null && it.quantity > 0) row.quantity = it.quantity;
    if (it.unit_price != null && it.unit_price > 0) row.unit_price = it.unit_price;
    if (it.product_id) row.product_id = it.product_id;
    out.push(row);
  }
  return out;
}
