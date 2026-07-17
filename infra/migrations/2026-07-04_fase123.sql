-- ============================================================================
-- SpesaSmart — Migrazione Fase 1/2/3 (2026-07-04)
--
-- STATO: GIÀ APPLICATA IN PRODUZIONE (Supabase). Questo file è la
-- documentazione della migrazione, non uno script da rilanciare — è comunque
-- idempotente (IF NOT EXISTS / DROP NOT NULL sono no-op se già applicati).
--
-- Contesto:
--  * prices.quarantined         — quarantena anomalie prezzo (Fase 1):
--                                 le righe quarantined=TRUE sono escluse da
--                                 ogni query di prezzi correnti/storici.
--  * shopping_lists.*           — "spesa abituale" (Fase 2): liste ricorrenti
--                                 ancorate a un'email (nessun login), digest
--                                 settimanale via scripts/weekly_digest.py.
--  * price_alerts.*             — "price watch" anonimi via email (Fase 2):
--                                 user_id diventa nullable (users è vuota),
--                                 l'email è la chiave; threshold_price
--                                 nullable = watch senza soglia.
-- ============================================================================

-- Fase 1: quarantena anomalie prezzo
ALTER TABLE prices ADD COLUMN IF NOT EXISTS quarantined boolean NOT NULL DEFAULT false;

-- Fase 2: spesa abituale (liste ricorrenti + digest email)
ALTER TABLE shopping_lists ADD COLUMN IF NOT EXISTS is_recurring boolean NOT NULL DEFAULT false;
ALTER TABLE shopping_lists ADD COLUMN IF NOT EXISTS digest_email varchar;
ALTER TABLE shopping_lists ADD COLUMN IF NOT EXISTS last_digest_at timestamptz;

-- Fase 2: price watch anonimi via email
ALTER TABLE price_alerts ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS email varchar;
ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS last_notified_at timestamptz;
ALTER TABLE price_alerts ALTER COLUMN threshold_price DROP NOT NULL;

-- ----------------------------------------------------------------------------
-- Indici consigliati (NON ancora applicati in produzione — applicare quando
-- i volumi lo richiedono):
--
-- CREATE INDEX IF NOT EXISTS idx_price_alerts_email
--     ON price_alerts (lower(email)) WHERE email IS NOT NULL;
-- CREATE INDEX IF NOT EXISTS idx_shopping_lists_digest_email
--     ON shopping_lists (lower(digest_email)) WHERE is_recurring = TRUE;
-- CREATE INDEX IF NOT EXISTS idx_prices_product_store_scraped
--     ON prices (product_id, store_id, scraped_at DESC) WHERE quarantined = FALSE;
-- ----------------------------------------------------------------------------
