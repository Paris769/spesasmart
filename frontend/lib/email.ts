/**
 * Email di riferimento dell'utente: e' la chiave di TUTTO cio' che e'
 * persistito lato server senza login (liste abituali, avvisi di prezzo,
 * storico spese). La chiave di localStorage e' la stessa usata da
 * /lista e da PriceWatch: cambiarla farebbe "perdere" l'utente.
 */
export const EMAIL_KEY = "spesasmart_email";

export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value.trim());
}

/** Email salvata su questo dispositivo, "" se assente o storage bloccato. */
export function readStoredEmail(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(EMAIL_KEY) || "";
  } catch {
    return ""; // storage non disponibile (private mode, cookie bloccati)
  }
}

export function storeEmail(value: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(EMAIL_KEY, value.trim());
  } catch {
    // storage non disponibile: l'email resta valida per la sessione
  }
}
