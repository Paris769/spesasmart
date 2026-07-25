/**
 * Posizione di riferimento condivisa quando l'utente non ha attivato il GPS.
 *
 * DEVE essere la stessa per la RICERCA prodotti e per il CALCOLO del piano:
 * se la ricerca fosse nazionale e il piano locale, l'autocomplete proporrebbe
 * prodotti che il piano poi dichiara "non trovati" (succedeva davvero).
 */
export const DEFAULT_LOCATION = { lat: 45.4642, lng: 9.19, label: "Milano" };
