# SpesaSmart - Direzioni di sviluppo

Data: 2026-07-08

Questo documento salva il lavoro svolto finora e chiarisce come evolvere il prodotto senza buttare nulla.

## Principio guida

Il comparatore costruito finora resta il motore dell'app. La nuova direzione non lo sostituisce: lo usa per rendere utile la spesa abituale, gli alert e il risparmio ricorrente.

## Opzione 1 - Comparatore affidabile

Stato: modulo gia avviato, da rafforzare.

Idea centrale: cercare un prodotto, vedere prezzo migliore, disponibilita, negozio/catena, storico e link ufficiale.

Cosa conservare:
- ricerca prodotto;
- confronto prezzi fra catene;
- disponibilita quando presente;
- dettaglio prezzi per shop;
- apertura sito ufficiale;
- agente che prepara lista e piano.

Cosa migliorare prima di spingere il prodotto:
- filtrare prezzi anomali e prezzi troppo vecchi;
- usare prezzo per unita quando disponibile;
- distinguere match certo, equivalente e generico;
- non promettere copertura piena su catene con pochi dati;
- mostrare disclaimer chiaro sui dati mancanti.

## Opzione 2 - Spesa abituale intelligente

Stato: direzione principale consigliata.

Idea centrale: l'utente salva la lista che compra ogni settimana; SpesaSmart controlla prezzi e alternative nel tempo e suggerisce il piano piu conveniente.

Cosa usa del comparatore:
- prodotti reali scelti dall'utente;
- prezzi correnti;
- piano automatico;
- storico prezzi;
- alert prezzo;
- promo check.

Primi task:
1. rendere la pagina Lista centrale nella navigazione;
2. salvare prodotti ancorati a product_id quando possibile;
3. calcolare piano subito e digest settimanale;
4. aggiungere alert/prezzo soglia sui prodotti preferiti;
5. introdurre etichette di affidabilita per ogni voce.

## Opzione 3 - Radar offerte e volantini

Stato: opzione futura.

Idea centrale: importare offerte da volantini e discount, geolocalizzarle e usarle per indicare cosa conviene questa settimana.

Quando svilupparla:
- dopo aver reso affidabile il comparatore;
- dopo aver attivato la spesa abituale;
- quando serve aumentare copertura discount e offerte locali.

Rischi:
- competizione diretta con app volantino gia forti;
- estrazione dati da PDF/immagini complessa;
- rischio errori su formati, multipack, sconti percentuali.

## Scelta operativa

Sviluppare in sequenza:

1. Base affidabile: pulizia dati, match, copertura dichiarata corretta.
2. Spesa abituale: lista ricorrente, digest, alert, promo score.
3. Radar offerte: modulo aggiuntivo, non sostitutivo.

## Regola di prodotto

Non mostrare un risparmio come certo se il confronto non e certo. Se il match e generico o il prezzo e sospetto, l'app deve dirlo chiaramente.
