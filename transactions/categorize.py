"""Guess an expense category from its description (and the bank's own category, if any).

Rules are checked in order, so more specific ones come first
(e.g. "uber eats" is food, but "uber" is transport).
Words of 6+ letters also match the start of a longer word, because statements
often glue words together ("ACADEMIAMOVIMENTO", "AMAZONMKTPLC").
"""

import re
import unicodedata

from .models import DefaultExpenseCategory as Category

RULES = [
    (Category.FEES, [
        "iof", "anuidade", "tarifa", "juros", "encargos", "multa", "annual fee", "late fee",
        "interest charge", "comision", "comisión",
    ]),
    (Category.SUBSCRIPTIONS, [
        "netflix", "spotify", "disney", "hbo", "max.com", "prime video", "amazon prime", "amazonprime",
        "youtub", "apple.com", "icloud", "google one", "adobe", "deezer", "xbox", "playstation",
        "microsoft", "dropbox", "paramount", "globoplay", "crunchyroll", "anthropic", "openai",
        "chatgpt", "assinatura", "suscripcion", "streaming",
    ]),
    (Category.SHOPPING, [
        "amazon", "mercado livre", "mercadolivre", "shopee", "aliexpress", "shein", "magalu",
        "americanas", "zara", "renner", "ikea", "ebay", "etsy", "store", "loja", "shop", "vestuario",
        "roupas", "departamento", "tienda", "ropa",
    ]),
    (Category.FOOD, [
        "uber eats", "ubereats", "ifood", "ifd", "rappi", "doordash", "restaurant", "restaurante",
        "lanchonete", "supermarket", "supermercado", "mercado", "mercearia", "grocery", "padaria",
        "bakery", "cafe", "coffee", "starbucks", "mcdonald", "burger", "pizza", "subway", "carrefour",
        "walmart", "whole foods", "trader joe",
    ]),
    (Category.TRANSPORT, [
        "uber", "lyft", "99app", "99pop", "cabify", "shell", "ipiranga", "petrobras", "posto",
        "combustivel", "fuel", "gas station", "parking", "estacionamento", "sem parar", "metro",
        "transporte", "airline", "latam", "azul", "zupper", "airbnb", "booking",
    ]),
    (Category.HEALTH, [
        "pharmacy", "farmacia", "drogasil", "droga raia", "drogaria", "cvs", "walgreens",
        "hospital", "clinic", "clinica", "dentist", "dentista", "gym", "academia", "smartfit", "saude",
        "salud",
    ]),
    (Category.ENTERTAINMENT, [
        "cinema", "cinemark", "ingresso", "ticketmaster", "steam", "bar", "pub", "theater",
        "teatro", "museum", "museu", "lazer", "entretenimento", "ocio",
    ]),
    (Category.HOUSING, [
        "rent", "aluguel", "alquiler", "condominio", "energia", "electric", "enel", "light", "sabesp",
        "water", "internet", "vivo", "claro", "tim", "comcast", "verizon", "at&t",
    ]),
]


def _normalize(text):
    """Lowercase and remove accents, so 'Farmácia' matches 'farmacia'."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _word_pattern(word):
    word = _normalize(word)
    end = "" if len(word) >= 6 else r"\b"
    return re.escape(word) + end


_PATTERNS = [
    (category, re.compile(r"\b(?:" + "|".join(_word_pattern(w) for w in words) + r")"))
    for category, words in RULES
]


def _match(text):
    text = _normalize(text)
    for category, pattern in _PATTERNS:
        if pattern.search(text):
            return category
    return None


def guess_category(description, bank_category=""):
    return _match(description) or (bank_category and _match(bank_category)) or Category.OTHER
