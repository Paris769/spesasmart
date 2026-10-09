/**
 * Posizione di riferimento condivisa quando l'utente non ha attivato il GPS.
 *
 * DEVE essere la stessa per la RICERCA prodotti e per il CALCOLO del piano:
 * se la ricerca fosse nazionale e il piano locale, l'autocomplete proporrebbe
 * prodotti che il piano poi dichiara "non trovati" (succedeva davvero).
 */
export const DEFAULT_LOCATION = { lat: 45.4642, lng: 9.19, label: "Milano" };

/**
 * Chiede la posizione al browser e la restituisce nel formato dello store.
 * Rigetta con un messaggio GIA' pronto da mostrare all'utente: i chiamanti
 * (assistente, pagina spese) non devono reinterpretare i codici di errore.
 */
export function requestBrowserLocation(
  timeoutMs = 10000
): Promise<{ lat: number; lng: number; label: string }> {
  return new Promise((resolve, reject) => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      reject(new Error("Questo browser non supporta la posizione: scegli una citta' dalla barra posizione."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Posizione attuale",
        }),
      () =>
        reject(
          new Error("Posizione non disponibile: puoi scegliere una citta' dalla barra posizione.")
        ),
      { timeout: timeoutMs, maximumAge: 60000 }
    );
  });
}
