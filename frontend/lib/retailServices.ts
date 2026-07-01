export type FulfillmentService = "delivery" | "pickup" | "courier_pickup";

export type RetailServiceConfig = {
  chainSlug: string;
  chainName: string;
  deliveryMin?: number | null;
  pickupMin?: number | null;
  services: FulfillmentService[];
  pickupDelegate: {
    enabled: boolean;
    label: string;
    partners: string[];
    note: string;
  };
  note: string;
};

export const SERVICE_PARTNERS = ["Deliveroo", "Uber Eats", "Glovo", "Stuart"];

export const RETAIL_SERVICE_CONFIG: RetailServiceConfig[] = [
  {
    chainSlug: "esselunga",
    chainName: "Esselunga",
    deliveryMin: 40,
    pickupMin: 40,
    services: ["delivery", "pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro delegato dopo login",
      partners: SERVICE_PARTNERS,
      note: "Da usare solo se il sito consente ritiro da parte di un incaricato o con delega dell'utente.",
    },
    note: "Soglie e slot possono variare per zona, negozio e disponibilita.",
  },
  {
    chainSlug: "carrefour",
    chainName: "Carrefour",
    deliveryMin: null,
    pickupMin: null,
    services: ["delivery", "pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro delegato verificabile",
      partners: SERVICE_PARTNERS,
      note: "Richiede controllo su nominativo ritiro e regole del punto vendita.",
    },
    note: "Minimo variabile per indirizzo, punto vendita, canale e promo.",
  },
  {
    chainSlug: "conad",
    chainName: "Conad",
    deliveryMin: null,
    pickupMin: null,
    services: ["delivery", "pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro delegato da verificare",
      partners: SERVICE_PARTNERS,
      note: "Le condizioni dipendono dal negozio Conad che evade l'ordine.",
    },
    note: "Servizi e minimi sono locali: dipendono dal negozio associato al CAP.",
  },
  {
    chainSlug: "coop",
    chainName: "Coop / EasyCoop",
    deliveryMin: null,
    pickupMin: null,
    services: ["delivery"],
    pickupDelegate: {
      enabled: false,
      label: "Non configurato",
      partners: [],
      note: "Attivabile solo dove esiste ritiro o delega supportata dal negozio.",
    },
    note: "Nel dataset attuale EasyCoop e offerte Coop sono gestite come disponibilita online/promozionale.",
  },
  {
    chainSlug: "iper",
    chainName: "Iper",
    deliveryMin: null,
    pickupMin: null,
    services: ["pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro delegato su punto vendita",
      partners: SERVICE_PARTNERS,
      note: "Da confermare nelle condizioni del negozio scelto.",
    },
    note: "Nel dataset attuale Iper e gestito soprattutto da offerte/volantino per punto vendita.",
  },
  {
    chainSlug: "pam",
    chainName: "Pam",
    deliveryMin: null,
    pickupMin: null,
    services: ["delivery", "pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro delegato verificabile",
      partners: SERVICE_PARTNERS,
      note: "Da confermare su Pam a Casa durante scelta slot e negozio.",
    },
    note: "Minimi e disponibilita variano per CAP e negozio.",
  },
  {
    chainSlug: "famila",
    chainName: "Famila / CosiComodo",
    deliveryMin: null,
    pickupMin: null,
    services: ["delivery", "pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro delegato da verificare",
      partners: SERVICE_PARTNERS,
      note: "Dipende dal negozio CosiComodo e dalla policy locale.",
    },
    note: "Catena collegata a CosiComodo: soglie e servizi sono locali.",
  },
  {
    chainSlug: "eurospin",
    chainName: "Eurospin",
    deliveryMin: null,
    pickupMin: null,
    services: ["pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro da incaricato",
      partners: SERVICE_PARTNERS,
      note: "Da usare come task manuale: non c'e integrazione ordine automatica completa.",
    },
    note: "Disponibilita attuale basata su offerte e punti vendita.",
  },
  {
    chainSlug: "lidl",
    chainName: "Lidl",
    deliveryMin: null,
    pickupMin: null,
    services: ["pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro da incaricato",
      partners: SERVICE_PARTNERS,
      note: "Valido come servizio esterno/manuale, non come carrello Lidl integrato.",
    },
    note: "Disponibilita attuale limitata alle offerte pubblicate online.",
  },
  {
    chainSlug: "aldi",
    chainName: "Aldi",
    deliveryMin: null,
    pickupMin: null,
    services: ["pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro da incaricato",
      partners: SERVICE_PARTNERS,
      note: "Valido come task manuale se il punto vendita lo consente.",
    },
    note: "Disponibilita attuale da volantino/offerte online.",
  },
  {
    chainSlug: "md",
    chainName: "MD",
    deliveryMin: null,
    pickupMin: null,
    services: ["pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro da incaricato",
      partners: SERVICE_PARTNERS,
      note: "Da verificare in base al punto vendita e alle regole di delega.",
    },
    note: "Disponibilita attuale da volantino strutturato MD.",
  },
  {
    chainSlug: "penny",
    chainName: "Penny",
    deliveryMin: null,
    pickupMin: null,
    services: ["pickup", "courier_pickup"],
    pickupDelegate: {
      enabled: true,
      label: "Ritiro da incaricato",
      partners: SERVICE_PARTNERS,
      note: "Da trattare come servizio esterno/manuale.",
    },
    note: "Disponibilita attuale da offerte pubblicate online.",
  },
];

export function minSpendLabel(value?: number | null) {
  return typeof value === "number" ? `min. EUR ${value.toFixed(0)}` : "minimo variabile";
}

export function serviceLabel(service: FulfillmentService) {
  if (service === "delivery") return "Consegna";
  if (service === "pickup") return "Ritiro";
  return "Ritiro con incaricato";
}