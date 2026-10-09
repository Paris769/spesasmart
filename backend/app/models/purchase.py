"""
Storico spese (Fase 3): la base dati su cui l'assistente AI costruisce
"rifai l'ultima spesa scegliendo le offerte".

Nessuna auth: come watches/recurring l'email e' la chiave (la tabella users e'
vuota). Una riga di `purchases` e' una spesa (scontrino caricato, piano
confermato o inserimento manuale), le righe di `purchase_items` sono gli
articoli, ancorati a products.id quando il match sul catalogo riesce.

Schema gia' migrato in produzione: questi modelli lo rispecchiano, non lo
creano. Gli endpoint (api/v1/endpoints/purchases.py) usano SQL raw con text()
come il resto del progetto; i modelli servono a script e job.
"""
import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Date, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.models.base import Base


class Purchase(Base):
    __tablename__ = "purchases"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Email in minuscolo: l'indice (email, purchase_date DESC NULLS LAST,
    # created_at DESC) e' su email "cosi' com'e'", quindi le query filtrano per
    # uguaglianza e la normalizzazione avviene in scrittura.
    email         = Column(String, nullable=False)
    chain_slug    = Column(String)
    store_id      = Column(UUID(as_uuid=True), ForeignKey("stores.id"))
    purchase_date = Column(Date)
    total         = Column(Numeric)
    # 'receipt' (scontrino via OCR) | 'plan' (piano ottimizzato confermato) | 'manual'
    source        = Column(String, nullable=False, default="manual", server_default="manual")
    # Payload originale: il JSON dello scontrino parsato o del piano, per poter
    # riprocessare il match in futuro senza perdere l'input.
    raw           = Column(JSONB)
    created_at    = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id      = Column(UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False)
    # NULL quando l'articolo non e' stato ancorato al catalogo (match_confidence='none')
    product_id       = Column(UUID(as_uuid=True), ForeignKey("products.id"))
    name             = Column(String, nullable=False)
    brand            = Column(String)
    quantity         = Column(Numeric, default=1, server_default="1")
    unit_price       = Column(Numeric)
    line_total       = Column(Numeric)
    # 'exact' (barcode o product_id fornito) | 'fuzzy' (match testuale) | 'none'
    match_confidence = Column(String, default="none", server_default="none")
