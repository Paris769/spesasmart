import axios from "axios";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1",
  headers: { "Content-Type": "application/json" },
});

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
  /** true se è un negozio virtuale di spesa online (consegna nazionale). */
  is_online: boolean;
}

// ── API calls ────────────────────────────────────────────────────────────────

export const searchProducts = (
  q: string,
  lat?: number,
  lng?: number,
  radiusKm?: number
) =>
  api
    .get<Product[]>("/products/search", {
      params: { q, limit: 100, lat, lng, radius_km: radiusKm },
    })
    .then((r) => r.data);

export const getProductPrices = (
  productId: string,
  lat: number,
  lng: number,
  radiusKm: number
) =>
  api
    .get<PriceResult[]>(`/products/${productId}/prices`, {
      params: { lat, lng, radius_km: radiusKm },
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
