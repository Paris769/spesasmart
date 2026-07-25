import json
import re
import unicodedata
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.freshness import fresh_price_sql
from app.db.session import get_db

router = APIRouter(prefix="/lists", tags=["lists"])

MIN_VALID_PRICE = 0.10


def _strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )

def _search_tokens(q: str) -> list[str]:
    normalized = _strip_accents(q.lower())
    return [tok for tok in re.findall(r"[a-z0-9]+", normalized) if len(tok) >= 2]


def _word_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if not tokens:
        return r"$^"
    parts: list[str] = []
    for tok in tokens:
        if tok == "caffe":
            parts.append(r"caff.")
        else:
            parts.append(re.escape(tok))
    return r"(^|[^[:alnum:]_])" + r"[[:space:][:punct:]]+".join(parts) + r"([^[:alnum:]_]|$)"


def _irrelevant_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if len(tokens) != 1:
        return r"$^"
    exclusions = {
        "caffe": [
            r"caffeina", r"yogurt", r"kefir", r"gelat[[:alnum:]_]*", r"cono", r"coppa", r"coppe", r"coppette",
            r"crema fredda", r"macchina", r"macchine", r"decalcificante", r"disincrostante",
            r"tazzin[[:alnum:]_]*", r"bicchier[[:alnum:]_]*", r"latte", r"ginseng", r"variegato", r"dessert", r"budino",
            r"affogato", r"fruyo", r"grisb.*", r"zero grassi", r"vasetto", r"mousse", r"cookies",
            r"cioccolato", r"cremosi", r"nocciola", r"vaniglia", r"stracciatella",
            r"yomo", r"muller", r"mÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¼ller", r"fage", r"sorbissimo", r"panna", r"gelateria",
            r"senza peccato", r"crema di", r"zuppalatte", r"colussi", r"cereali", r"orzo",
            r"biscott[[:alnum:]_]*", r"liquore", r"estratto", r"cacao", r"amaro",
        ],
        "latte": [r"panna", r"dessert",
            r"detergente", r"corpo", r"crema", r"bagnoschiuma", r"pan", r"biscott",
            r"gelat", r"yogurt", r"kefir", r"cioccolat", r"macchiato", r"fiocco", r"fiocchi",
            # Formaggi il cui nome commerciale INIZIA con "Latte ..." (es. "Latte
            # Montagna Alto Adige Stelvio DOP", ~20 EUR): il match testuale li
            # prendeva come latte, falsando il piano.
            r"stelvio", r"dop", r"formagg[[:alnum:]_]*", r"stagionat[[:alnum:]_]*",
            r"asiago", r"fontina", r"caciotta", r"toma",
        ],
        "acqua": [r"bibita", r"energy drink", r"red bull", r"fruity", r"aromatizzat[[:alnum:]_]*", r"limone", r"micellare", r"profumo", r"detergente", r"colonia", r"ossigenata", r"patch", r"hydrogel", r"contorno occhi", r"peonia", r"mask", r"demineralizzat[[:alnum:]_]*", r"bagnodoccia", r"doccia", r"shampoo", r"cetriolo"],
        "pasta": [r"bris.e", r"sfoglia", r"frolla", r"pizza", r"lievitat[[:alnum:]_]*", r"raviol[[:alnum:]_]*", r"tortell[[:alnum:]_]*", r"cappellett[[:alnum:]_]*", r"gnocch[[:alnum:]_]*", r"ripien[[:alnum:]_]*", r"pappa", r"pastina", r"lasagn[[:alnum:]_]*", r"cannellon[[:alnum:]_]*", r"dentifric[[:alnum:]_]*", r"placca", r"carie", r"antitartaro", r"collutor[[:alnum:]_]*", r"capitano"],
        "olio": [r"motor[[:alnum:]_]*", r"motore", r"benzina", r"diesel", r"15w", r"10w", r"5w", r"lubrificant[[:alnum:]_]*", r"shell", r"helix", r"detergente", r"doccia", r"eucerin"],
        "riso": [r"aceto", r"salsa", r"chips", r"gallo.s chips", r"gallette", r"snack", r"barrett[[:alnum:]_]*", r"soffiato", r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "pollo": [r"saikebon", r"noodle[[:alnum:]_]*", r"noodles", r"instant", r"adoc", r"day by day", r"gattin[[:alnum:]_]*", r"gatto", r"gatti", r"pet food", r"wurstel", r"w.rstel", r"tacchino", r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "petto": [r"saikebon", r"noodle[[:alnum:]_]*", r"noodles", r"instant", r"adoc", r"day by day", r"gattin[[:alnum:]_]*", r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "tonno": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*", r"petfriends", r"surimi", r"granchio"],
        "insalata": [r"russa", r"capricciosa", r"maionese", r"di riso", r"di pasta", r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "pomodori": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "mele": [r"aceto", r"salsa", r"succo", r"nettare", r"omogeneizzat[[:alnum:]_]*", r"confettura", r"composta", r"biscott[[:alnum:]_]*", r"grancereale", r"mirtilli", r"nocciol[[:alnum:]_]*"],
    }
    parts = exclusions.get(tokens[0], [])
    if not parts:
        return r"$^"
    return r"(^|[^[:alnum:]_])(" + "|".join(parts) + r")([^[:alnum:]_]|$)"


def _has_irrelevant_terms(q: str) -> bool:
    return _irrelevant_regex(q) != r"$^"


def _required_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if len(tokens) != 1:
        return r"$^"
    required = {
        "caffe": [
            r"arabica", r"grani", r"macinat[[:alnum:]_]*", r"solubil[[:alnum:]_]*",
            r"espresso", r"ciald[[:alnum:]_]*", r"capsul[[:alnum:]_]*", r"classico",
            r"classic", r"filtro", r"tradition", r"deka", r"decaffeinat[[:alnum:]_]*",
            r"americano", r"coffee",
        ],
        "riso": [r"riso", r"risott[[:alnum:]_]*", r"carnaroli", r"arborio", r"basmati", r"roma", r"originario", r"parboiled"],
        "pollo": [r"pollo", r"petto", r"cosc[[:alnum:]_]*", r"sovracos[[:alnum:]_]*", r"fusi", r"bocconcini", r"filett[[:alnum:]_]*", r"arrosto"],
        "tonno": [r"tonno", r"filett[[:alnum:]_]*", r"tranc[[:alnum:]_]*", r"rio mare", r"nostromo", r"mareblu", r"as do mar"],
        "insalata": [r"insalat[[:alnum:]_]*", r"lattuga", r"gentile", r"iceberg", r"misticanza", r"rucola", r"valeriana", r"songino", r"belga"],
        "pomodori": [r"pomodor[[:alnum:]_]*", r"pelati", r"passata", r"datterini", r"ciliegini"],
        "mele": [r"mele", r"mela", r"golden", r"gala", r"fuji", r"renetta", r"granny"],
        "acqua": [r"acqua", r"naturale", r"frizzante", r"effervescente", r"minerale"],
    }
    parts = required.get(tokens[0], [])
    if not parts:
        return r"$^"
    return r"(^|[^[:alnum:]_])(" + "|".join(parts) + r")([^[:alnum:]_]|$)"


def _has_required_terms(q: str) -> bool:
    return _required_regex(q) != r"$^"


# ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Schemi ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬

class ListCreate(BaseModel):
    name: str = "Lista spesa"


class ItemAdd(BaseModel):
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    quantity: float = 1
    unit: Optional[str] = None


class OptimizeRequest(BaseModel):
    lat: float
    lng: float
    radius_km: float = 5.0
    max_stores: int = 2          # quanti negozi al massimo nella soluzione


class QuickItem(BaseModel):
    query: str                   # testo libero, es. "latte", "pasta barilla"
    quantity: float = 1
    # Se l'utente ha SCELTO un prodotto reale dall'autocomplete, ne arriva l'id:
    # in quel caso confrontiamo ESATTAMENTE quel prodotto tra i negozi (apples
    # to apples), invece del match testuale "il piu' economico che assomiglia".
    product_id: Optional[str] = None


class QuickOptimizeRequest(BaseModel):
    items: list[QuickItem]
    lat: float
    lng: float
    radius_km: float = 5.0
    # Strategia di ottimizzazione (Fase 1 auto-carrello):
    #   "cheapest"      → minimizza il totale (split multi-negozio): default
    #   "fewest_stores" → preferisci un solo negozio (best_single)
    #   "availability"  → preferisci prodotti disponibili (in_stock) poi prezzo
    # Nota: cheapest e fewest_stores sono già entrambi nel risultato; strategy
    # cambia solo la selezione per-voce quando è "availability" e viene rimandata
    # al client come suggerimento su quale piano evidenziare.
    strategy: str = "cheapest"


# ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Endpoint ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬

@router.get("/")
async def get_lists(user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT * FROM shopping_lists WHERE user_id = :uid ORDER BY created_at DESC"),
        {"uid": user_id},
    )
    return [dict(r) for r in result.mappings().all()]


@router.post("/")
async def create_list(body: ListCreate, user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("INSERT INTO shopping_lists (user_id, name) VALUES (:uid, :name) RETURNING *"),
        {"uid": user_id, "name": body.name},
    )
    await db.commit()
    return dict(result.mappings().first())


@router.get("/{list_id}")
async def get_list(list_id: str, db: AsyncSession = Depends(get_db)):
    lst = await db.execute(
        text("SELECT * FROM shopping_lists WHERE id = :id"),
        {"id": list_id},
    )
    row = lst.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Lista non trovata")

    items = await db.execute(
        text("""
            SELECT li.*, p.name AS product_name_db, p.image_url, p.unit AS product_unit
            FROM list_items li
            LEFT JOIN products p ON li.product_id = p.id
            WHERE li.list_id = :lid
            ORDER BY li.sort_order
        """),
        {"lid": list_id},
    )
    return {**dict(row), "items": [dict(i) for i in items.mappings().all()]}


@router.post("/{list_id}/items")
async def add_item(list_id: str, body: ItemAdd, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            INSERT INTO list_items (list_id, product_id, product_name, quantity, unit)
            VALUES (:lid, :pid, :pname, :qty, :unit)
            RETURNING *
        """),
        {
            "lid": list_id,
            "pid": body.product_id,
            "pname": body.product_name,
            "qty": body.quantity,
            "unit": body.unit,
        },
    )
    await db.commit()
    return dict(result.mappings().first())


@router.delete("/{list_id}/items/{item_id}", status_code=204)
async def remove_item(list_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("DELETE FROM list_items WHERE id = :iid AND list_id = :lid"),
        {"iid": item_id, "lid": list_id},
    )
    await db.commit()


# Igiene dati (vedi core/freshness): nei piani di ottimizzazione entrano solo
# prezzi non in quarantena e abbastanza freschi per la loro catena. Il
# frammento SQL e i suoi bind param sono deterministici: costruiti una volta
# a livello di modulo e riusati a ogni richiesta.
_FRESH_PARAMS: dict = {}
_FRESH_SQL = fresh_price_sql(_FRESH_PARAMS, price_alias="pr", chain_alias="c")

# Query SQL: per ogni negozio, il prodotto piu' economico che matcha il testo.
# DISTINCT ON (s.id) + ORDER BY s.id, price ASC = 1 riga/negozio = il piu' barato.
_QUICK_ITEM_SQL = text(f"""
    WITH candidates AS MATERIALIZED (
        SELECT p.*,
               CASE
                   WHEN lower(p.name) = :q_lower THEN 10
                   WHEN lower(p.name) LIKE :q_start THEN 9
                   WHEN lower(p.name) ~ :q_word_re THEN 8
                   WHEN lower(COALESCE(p.brand, '')) ~ :q_word_re THEN 7
                   WHEN to_tsvector('simple', lower(p.name || ' ' || COALESCE(p.brand, '')))
                        @@ plainto_tsquery('simple', :q_tsquery) THEN 6
                   ELSE 1
               END AS match_rank
        FROM products p
        WHERE (
              lower(p.name) ~ :q_word_re
              OR lower(COALESCE(p.brand, '')) ~ :q_word_re
              OR to_tsvector('simple', lower(p.name || ' ' || COALESCE(p.brand, '')))
                    @@ plainto_tsquery('simple', :q_tsquery)
            )
          AND NOT (:has_irrelevant AND lower(p.name || ' ' || COALESCE(p.brand, '')) ~ :irrelevant_re)
          AND NOT (:has_required AND lower(p.name || ' ' || COALESCE(p.brand, '')) !~ :required_re)
        ORDER BY match_rank DESC, similarity(p.name, :q) DESC, p.updated_at DESC NULLS LAST
        LIMIT 80
    )
    SELECT DISTINCT ON (s.id)
        s.id::text          AS store_id,
        s.name              AS store_name,
        c.name              AS chain_name,
        c.slug              AS chain_slug,
        c.shop_url          AS shop_url,
        s.has_delivery      AS has_delivery,
        s.has_click_collect AS has_click_collect,
        (s.external_id LIKE '%-online') AS is_online,
        CASE WHEN s.external_id LIKE '%-online' THEN NULL
             ELSE ROUND(ST_Distance(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography
                  )::numeric / 1000, 2)
        END                 AS distance_km,
        pr.price            AS price,
        pr.price_per_unit   AS price_per_unit,
        pr.product_url      AS product_url,
        p.id::text          AS product_id,
        p.name              AS product_name,
        p.image_url         AS image_url,
        pr.in_stock         AS in_stock
    FROM candidates p
    JOIN prices pr ON pr.product_id = p.id AND pr.is_current = TRUE
    JOIN stores s  ON pr.store_id = s.id   AND s.is_active = TRUE
    JOIN chains c  ON s.chain_id = c.id
    WHERE pr.price >= :min_valid_price
      AND NOT pr.quarantined
      AND {_FRESH_SQL}
      AND (
            s.external_id LIKE '%-online'
            OR ST_DWithin(
                 s.coordinates::geography,
                 ST_Point(:lng, :lat)::geography,
                 :radius_m
               )
          )
    -- Con strategia "availability" (:prefer_stock) il prodotto in stock vince a
    -- parità di pertinenza; altrimenti conta solo prezzo. NULL trattato come
    -- disponibile (nessuna info negativa).
    ORDER BY s.id,
             p.match_rank DESC,
             (CASE WHEN :prefer_stock THEN COALESCE(pr.in_stock, TRUE) ELSE TRUE END) DESC,
             pr.price ASC
""")

# Variante per prodotto SCELTO: stesso prodotto (p.id) confrontato tra i negozi.
_QUICK_ITEM_BY_ID_SQL = text(f"""
    SELECT DISTINCT ON (s.id)
        s.id::text          AS store_id,
        s.name              AS store_name,
        c.name              AS chain_name,
        c.slug              AS chain_slug,
        c.shop_url          AS shop_url,
        s.has_delivery      AS has_delivery,
        s.has_click_collect AS has_click_collect,
        (s.external_id LIKE '%-online') AS is_online,
        CASE WHEN s.external_id LIKE '%-online' THEN NULL
             ELSE ROUND(ST_Distance(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography
                  )::numeric / 1000, 2)
        END                 AS distance_km,
        pr.price            AS price,
        pr.price_per_unit   AS price_per_unit,
        pr.product_url      AS product_url,
        p.id::text          AS product_id,
        p.name              AS product_name,
        p.image_url         AS image_url,
        pr.in_stock         AS in_stock
    FROM products p
    JOIN prices pr ON pr.product_id = p.id AND pr.is_current = TRUE
    JOIN stores s  ON pr.store_id = s.id   AND s.is_active = TRUE
    JOIN chains c  ON s.chain_id = c.id
    WHERE pr.price >= :min_valid_price
      AND NOT pr.quarantined
      AND {_FRESH_SQL}
      AND p.id = :pid
      AND (
            s.external_id LIKE '%-online'
            OR ST_DWithin(
                 s.coordinates::geography,
                 ST_Point(:lng, :lat)::geography,
                 :radius_m
               )
          )
    ORDER BY s.id,
             (CASE WHEN :prefer_stock THEN COALESCE(pr.in_stock, TRUE) ELSE TRUE END) DESC,
             pr.price ASC
""")


@router.post("/optimize-quick")
async def optimize_quick(body: QuickOptimizeRequest, db: AsyncSession = Depends(get_db)):
    """
    Ottimizzatore STATELESS della lista (nessun login, nessuna lista salvata).
    Input: lista di voci in testo libero + posizione. Per ogni voce trova il
    prodotto piu' economico che la soddisfa in ciascun negozio vicino (o online),
    poi calcola:
      ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â¢ best_single  ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â il negozio singolo che copre piu' voci al minor costo;
      ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â¢ single_ranking ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â i primi negozi per costo totale (trasparenza);
      ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â¢ multi_store  ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â split goloso: ogni voce dal negozio piu' economico;
      ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â¢ savings      ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â risparmio del multi vs il miglior singolo.
    Ogni voce porta il deep-link al prodotto (product_url) per l'acquisto 1-tap.
    """
    items = [it for it in body.items if it.query and len(it.query.strip()) >= 2][:40]
    if not items:
        raise HTTPException(status_code=400, detail="Fornire almeno una voce valida")

    strategy = body.strategy if body.strategy in ("cheapest", "fewest_stores", "availability") else "cheapest"
    prefer_stock = strategy == "availability"
    radius_m = body.radius_km * 1000
    # stores[sid] = meta + righe per voce; per_item[i] = miglior prezzo globale
    stores: dict[str, dict] = {}
    per_item_best: list[Optional[dict]] = []
    not_found: list[str] = []

    for idx, it in enumerate(items):
        q = it.query.strip()
        qty = float(it.quantity or 1)

        # Prodotto scelto dall'utente ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ confronto esatto per id (se l'id ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨ un UUID
        # valido). Se non trova prezzi nel raggio, ricade sul match testuale.
        pid_valid = None
        if it.product_id:
            try:
                pid_valid = str(UUID(it.product_id))
            except (ValueError, AttributeError, TypeError):
                pid_valid = None

        # match_type: "exact" = confronto sul product_id scelto dal client;
        # "text" = match testuale (campo additivo, il frontend lo legge opzionale).
        match_type = "text"
        rows = []
        if pid_valid:
            rows = (await db.execute(_QUICK_ITEM_BY_ID_SQL, {
                "pid": pid_valid,
                "lat": body.lat, "lng": body.lng, "radius_m": radius_m, "min_valid_price": MIN_VALID_PRICE,
                "prefer_stock": prefer_stock,
                **_FRESH_PARAMS,
            })).mappings().all()
            if rows:
                match_type = "exact"

        if not rows:
            ql = q.lower()
            rows = (await db.execute(_QUICK_ITEM_SQL, {
                "q": q,
                "q_tsquery": " ".join(_search_tokens(q)) or ql,
                "q_word_re": _word_regex(q),
                "irrelevant_re": _irrelevant_regex(q),
                "has_irrelevant": _has_irrelevant_terms(q),
                "required_re": _required_regex(q),
                "has_required": _has_required_terms(q),
                "q_lower": ql, "q_start": f"{ql} %",
                "q_mid": f"% {ql} %", "q_end": f"% {ql}",
                "lat": body.lat, "lng": body.lng, "radius_m": radius_m, "min_valid_price": MIN_VALID_PRICE,
                "prefer_stock": prefer_stock,
                **_FRESH_PARAMS,
            })).mappings().all()

        if not rows:
            not_found.append(q)
            per_item_best.append(None)
            continue

        # miglior riga globale per questa voce (per lo split multi-negozio).
        # Con strategia "availability" prima gli in-stock, poi il prezzo.
        def _rank(r):
            in_stock = r["in_stock"] if r["in_stock"] is not None else True
            stock_pen = 0 if in_stock else 1
            return (stock_pen, float(r["price"])) if prefer_stock else (float(r["price"]),)
        best_row = min(rows, key=_rank)
        per_item_best.append({
            "query": q, "quantity": qty,
            "price": float(best_row["price"]),
            "price_per_unit": float(best_row["price_per_unit"]) if best_row["price_per_unit"] is not None else None,
            "subtotal": round(float(best_row["price"]) * qty, 2),
            "store_id": best_row["store_id"],
            "chain_name": best_row["chain_name"],
            "chain_slug": best_row["chain_slug"],
            "store_name": best_row["store_name"],
            "shop_url": best_row["shop_url"],
            "has_delivery": best_row["has_delivery"],
            "has_click_collect": best_row["has_click_collect"],
            "product_url": best_row["product_url"],
            "product_name": best_row["product_name"],
            "in_stock": best_row["in_stock"],
            "match_type": match_type,
            "matched_product_id": best_row["product_id"],
            "matched_product_name": best_row["product_name"],
        })

        for r in rows:
            sid = r["store_id"]
            st = stores.setdefault(sid, {
                "store_id": sid,
                "store_name": r["store_name"],
                "chain_name": r["chain_name"],
                "chain_slug": r["chain_slug"],
                "shop_url": r["shop_url"],
                "has_delivery": r["has_delivery"],
                "has_click_collect": r["has_click_collect"],
                "is_online": r["is_online"],
                "distance_km": float(r["distance_km"]) if r["distance_km"] is not None else None,
                "total": 0.0,
                "covered": 0,
                "items": [],
            })
            sub = round(float(r["price"]) * qty, 2)
            st["total"] = round(st["total"] + sub, 2)
            st["covered"] += 1
            st["items"].append({
                "query": q, "quantity": qty,
                "price": float(r["price"]), "subtotal": sub,
                "price_per_unit": float(r["price_per_unit"]) if r["price_per_unit"] is not None else None,
                "product_name": r["product_name"],
                "product_url": r["product_url"],
                "image_url": r["image_url"],
                "in_stock": r["in_stock"],
                "match_type": match_type,
                "matched_product_id": r["product_id"],
                "matched_product_name": r["product_name"],
            })

    n_items = len(items)
    n_findable = sum(1 for b in per_item_best if b is not None)

    # Ranking single-store: prima chi copre piu' voci, poi chi costa meno.
    ranking = sorted(stores.values(), key=lambda s: (-s["covered"], s["total"]))
    best_single = ranking[0] if ranking else None

    # Il confronto e' utile se mostra CATENE diverse: con catene molto capillari
    # (es. Famila, decine di negozi) i primi 5 erano tutti lo stesso marchio,
    # nascondendo le alternative reali. Teniamo il miglior negozio per catena.
    by_chain: dict[str, dict] = {}
    for s in ranking:
        by_chain.setdefault(s["chain_slug"] or s["store_id"], s)
    chain_ranking = list(by_chain.values())

    # Split multi-negozio (goloso, per voce piu' economica)
    multi_by_store: dict[str, dict] = {}
    multi_total = 0.0
    for b in per_item_best:
        if not b:
            continue
        sid = b["store_id"]
        ms = multi_by_store.setdefault(sid, {
            "store_id": sid, "store_name": b["store_name"],
            "chain_name": b["chain_name"], "chain_slug": b["chain_slug"],
            "shop_url": b["shop_url"], "has_delivery": b["has_delivery"],
            "has_click_collect": b["has_click_collect"],
            "subtotal": 0.0, "items": [],
        })
        ms["subtotal"] = round(ms["subtotal"] + b["subtotal"], 2)
        multi_total = round(multi_total + b["subtotal"], 2)
        ms["items"].append(b)

    # Risparmio: confronto solo se il singolo migliore copre tutte le voci trovabili.
    savings = 0.0
    if best_single and best_single["covered"] == n_findable and n_findable > 0:
        savings = round(best_single["total"] - multi_total, 2)

    # Numero di voci "disponibili" (in stock) nel piano multi-negozio: informa
    # l'utente sull'esito reale della strategia "availability".
    in_stock_count = sum(
        1 for b in per_item_best if b and (b["in_stock"] is None or b["in_stock"])
    )

    # Piano consigliato in base alla strategia: "single" (meno negozi) o
    # "multi" (prezzo più basso / disponibilità, che sfruttano lo split).
    recommended = "single" if strategy == "fewest_stores" else "multi"

    return {
        "n_items": n_items,
        "n_findable": n_findable,
        "strategy": strategy,
        "recommended_plan": recommended,
        "in_stock_count": in_stock_count,
        "best_single": best_single,
        "single_ranking": chain_ranking[:6],
        "multi_store": {
            "total": multi_total,
            "stores": sorted(multi_by_store.values(), key=lambda s: -s["subtotal"]),
            "savings_vs_single": savings if savings > 0 else 0.0,
        },
        "not_found": not_found,
    }


@router.post("/{list_id}/optimize")
async def optimize_list(list_id: str, body: OptimizeRequest, db: AsyncSession = Depends(get_db)):
    """
    Algoritmo di ottimizzazione:
    1. Recupera tutti gli item della lista con product_id
    2. Per ogni prodotto trova i prezzi nei negozi nel raggio
    3. Calcola la soluzione a singolo negozio (totale piÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¹ basso)
    4. Calcola la soluzione multi-negozio (max_stores) se conviene
    5. Restituisce entrambe le opzioni con i totali
    """
    items_res = await db.execute(
        text("SELECT * FROM list_items WHERE list_id = :lid AND product_id IS NOT NULL"),
        {"lid": list_id},
    )
    items = items_res.mappings().all()

    if not items:
        raise HTTPException(status_code=400, detail="Nessun prodotto con ID nella lista")

    # Aggrega i duplicati per product_id (stessa voce ripetuta: le quantita'
    # si sommano); tutti i conteggi a valle usano i prodotti DISTINTI.
    quantities: dict[str, float] = {}
    for i in items:
        pid = str(i["product_id"])
        quantities[pid] = quantities.get(pid, 0.0) + float(i["quantity"])
    product_ids = list(quantities.keys())

    # Prezzi per ogni prodotto nei negozi vicini. Igiene dati: esclusi i
    # prezzi in quarantena e quelli stantii per la loro catena.
    opt_params: dict = {
        "pids": product_ids,
        "lat": body.lat,
        "lng": body.lng,
        "radius_m": body.radius_km * 1000,
        "min_valid_price": MIN_VALID_PRICE,
    }
    fresh_opt = fresh_price_sql(opt_params, price_alias="p", chain_alias="c")
    prices_res = await db.execute(
        text(f"""
            SELECT
                p.product_id::text, p.price, p.price_per_unit,
                s.id::text AS store_id, s.name AS store_name,
                c.name AS chain_name, c.slug AS chain_slug,
                c.shop_url, s.has_delivery, s.has_click_collect,
                ROUND(ST_Distance(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography
                )::numeric / 1000, 2) AS distance_km
            FROM prices p
            JOIN stores s ON p.store_id = s.id
            JOIN chains c ON s.chain_id = c.id
            WHERE p.product_id = ANY(CAST(:pids AS uuid[]))
              AND p.is_current = TRUE
              AND NOT p.quarantined
              AND p.price >= :min_valid_price
              AND {fresh_opt}
              AND s.is_active  = TRUE
              AND ST_DWithin(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography,
                    :radius_m
                  )
            ORDER BY p.price
        """),
        opt_params,
    )
    all_prices = prices_res.mappings().all()

    # Indicizza: product_id ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ lista prezzi (giÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  ordinata per prezzo asc)
    by_product: dict[str, list] = {}
    for row in all_prices:
        pid = row["product_id"]
        by_product.setdefault(pid, []).append(dict(row))

    # ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Opzione 1: singolo negozio ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬
    # Per ogni negozio calcola il totale sommando il miglior prezzo disponibile
    store_totals: dict[str, dict] = {}
    for pid, price_rows in by_product.items():
        qty = quantities.get(pid, 1)
        for row in price_rows:
            sid = row["store_id"]
            store_totals.setdefault(sid, {
                "store_id": sid,
                "store_name": row["store_name"],
                "chain_name": row["chain_name"],
                "chain_slug": row["chain_slug"],
                "shop_url": row["shop_url"],
                "has_delivery": row["has_delivery"],
                "has_click_collect": row["has_click_collect"],
                "distance_km": row["distance_km"],
                "total": 0.0,
                "covered_products": 0,
                "items": [],
            })
            entry = store_totals[sid]
            # aggiunge solo se non ancora assegnato per questo prodotto
            already = next((x for x in entry["items"] if x["product_id"] == pid), None)
            if not already:
                entry["total"] += float(row["price"]) * qty
                entry["covered_products"] += 1
                entry["items"].append({
                    "product_id": pid,
                    "price": float(row["price"]),
                    "price_per_unit": float(row["price_per_unit"]) if row["price_per_unit"] is not None else None,
                    "quantity": qty,
                    "subtotal": float(row["price"]) * qty,
                    # gli item di /optimize hanno sempre un product_id reale
                    "match_type": "exact",
                })

    n_products = len(product_ids)
    # solo negozi che coprono tutti i prodotti
    complete_stores = [
        s for s in store_totals.values() if s["covered_products"] == n_products
    ]
    best_single = min(complete_stores, key=lambda x: x["total"]) if complete_stores else None

    # ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ Opzione 2: multi-negozio greedy ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬
    # Assegna ogni prodotto al negozio piÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¹ economico (indipendentemente)
    best_per_product = {}
    for pid, price_rows in by_product.items():
        if price_rows:
            best_per_product[pid] = price_rows[0]  # giÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  sorted ASC

    multi_store_assignment: dict[str, dict] = {}
    multi_total = 0.0
    for pid, row in best_per_product.items():
        qty = quantities.get(pid, 1)
        sid = row["store_id"]
        multi_store_assignment.setdefault(sid, {
            "store_id": sid,
            "store_name": row["store_name"],
            "chain_name": row["chain_name"],
            "shop_url": row["shop_url"],
            "has_delivery": row["has_delivery"],
            "distance_km": row["distance_km"],
            "subtotal": 0.0,
            "items": [],
        })
        subtotal = float(row["price"]) * qty
        multi_total += subtotal
        multi_store_assignment[sid]["subtotal"] += subtotal
        multi_store_assignment[sid]["items"].append({
            "product_id": pid,
            "price": float(row["price"]),
            "price_per_unit": float(row["price_per_unit"]) if row["price_per_unit"] is not None else None,
            "quantity": qty,
            "subtotal": subtotal,
            "match_type": "exact",
        })

    result = {
        "single_store": best_single,
        "multi_store": {
            "total": round(multi_total, 2),
            "stores": list(multi_store_assignment.values()),
            "savings_vs_single": round(
                (best_single["total"] - multi_total) if best_single else 0, 2
            ),
        },
        "products_without_prices": [
            pid for pid in product_ids if pid not in by_product
        ],
    }

    # Salva risultato nella lista (colonna JSONB ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ JSON valido, non str(dict))
    await db.execute(
        text("UPDATE shopping_lists SET optimization_result = CAST(:res AS jsonb) WHERE id = :lid"),
        {"res": json.dumps(result, default=str), "lid": list_id},
    )
    await db.commit()
    return result
