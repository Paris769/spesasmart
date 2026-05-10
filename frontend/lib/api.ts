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
  distance_km: number;
}

// ── API calls ────────────────────────────────────────────────────────────────

export const searchProducts = (q: string) =>
  api.get<Product[]>("/products/search", { params: { q } }).then((r) => r.data);

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
