"""Інвайт-коди команд: без 0/O/1/I, генеруються криптографічно."""

import secrets

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def looks_like_code(text: str) -> bool:
    text = text.strip().upper()
    return len(text) == CODE_LENGTH and all(ch in ALPHABET for ch in text)
