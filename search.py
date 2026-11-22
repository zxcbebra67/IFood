import os
import re
from difflib import SequenceMatcher


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def words(text: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9]+", normalize(text))


def word_match(q: str, w: str) -> float:
    """1.0 — точное совпадение, 0.8 — словоформа или опечатка, 0.0 — не совпало."""
    if q == w:
        return 1.0
    # словоформы: «курицы», «риса» — общее начало и близкая длина
    prefix = len(os.path.commonprefix([q, w]))
    if prefix >= max(3, min(len(q), len(w)) - 2) and abs(len(q) - len(w)) <= 3:
        return 0.8
    # опечатки в длинных словах: «курицца»; короткие слова так не сравниваем,
    # чтобы не смешивать «рис» и «сыр»
    if len(q) >= 5 and len(w) >= 5 and SequenceMatcher(None, q, w).ratio() >= 0.85:
        return 0.8
    return 0.0


def _line_score(query_words: list[str], line_words: list[str]) -> float:
    total = 0.0
    for q in query_words:
        best = max((word_match(q, w) for w in line_words), default=0.0)
        if best == 0.0:
            return 0.0  # все слова фразы («куриное филе») должны найтись в строке
        total += best
    return total / len(query_words)


def ingredient_score(phrase: str, lines: list[str]) -> float:
    query_words = words(phrase)
    if not query_words:
        return 0.0
    return max((_line_score(query_words, words(line)) for line in lines), default=0.0)


def match_ingredients(required: list[str], lines: list[str]) -> float | None:
    """None, если хоть один обязательный продукт не найден, иначе средняя оценка."""
    scores = [ingredient_score(p, lines) for p in required]
    if not scores or min(scores) == 0.0:
        return None
    return sum(scores) / len(scores)
