"""Service: extração de palavras-chave de uma pergunta em linguagem natural,
usada pela busca por palavras-chave (ver `retrieval_service.py`).
"""
import re
import unicodedata

from models import KEYWORD_MAX_TERMS, KEYWORD_MIN_LEN, STOPWORDS

_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ][0-9A-Za-zÀ-ÖØ-öø-ÿ_'.\-]*")
# Token só de dígitos (com separador decimal opcional: "3", "2.34", "61850").
# Um alfanumérico misto ("IEC61850", "v2") não cai aqui — já é uma palavra só.
_NUMBER_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)*$")

_STOPWORDS_NORM = {
    "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))
    for word in STOPWORDS
}


def _strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _is_number_token(token):
    return bool(_NUMBER_RE.match(token))


def _merge_number_tokens(tokens):
    """Funde um número sozinho ("3", "2.34") com a palavra que o precede
    ("Tabela 5", "IEC 61850") num candidato composto, desde que essa palavra
    não seja ela mesma um número ou uma stopword (evita "3 4 5", "para 3")."""
    merged = []
    skip_next = False
    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        next_token = tokens[i + 1] if i + 1 < len(tokens) else None
        norm_token = _strip_accents(token.lower())
        if (next_token and _is_number_token(next_token) and not _is_number_token(token)
                and norm_token not in _STOPWORDS_NORM):
            merged.append(f"{token} {next_token}")
            skip_next = True
        else:
            merged.append(token)
    return merged


class KeywordService:
    """Extrai as palavras-chave de uma pergunta, pra virarem consultas
    independentes ao RAG (ver RetrievalService.retrieve_rows)."""

    @staticmethod
    def extract(question, max_terms=KEYWORD_MAX_TERMS, min_len=KEYWORD_MIN_LEN):
        """Quebra a pergunta em palavras e devolve as candidatas a
        palavra-chave, na ordem em que aparecem, sem repetição e sem as
        palavras vazias. Números soltos são fundidos com a palavra à
        esquerda (ver _merge_number_tokens); os que sobrarem sem fusão são
        descartados. Siglas com dígito ("Fig.2") passam mesmo com menos de
        min_len letras. Mantém a grafia original (acento importa pro
        embedding)."""
        raw_tokens = [t for t in (raw.strip("-.'_") for raw in _TOKEN_RE.findall(question or "")) if t]
        candidates = _merge_number_tokens(raw_tokens)

        keywords = []
        seen = set()
        for token in candidates:
            is_compound = " " in token
            head = token.split(" ", 1)[0] if is_compound else token
            norm_head = _strip_accents(head.lower())
            norm_full = _strip_accents(token.lower())
            if not is_compound:
                if _is_number_token(token):
                    continue
                if norm_head in _STOPWORDS_NORM:
                    continue
                if len(norm_head) < min_len and not any(ch.isdigit() for ch in norm_head):
                    continue
            if norm_full in seen:
                continue
            seen.add(norm_full)
            keywords.append(token)
            if len(keywords) >= max_terms:
                break
        return keywords
