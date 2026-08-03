from app.services.validators import (
    ban_reason,
    format_short_name,
    has_forbidden_scheme,
    has_url,
    normalize_phone,
    parse_member_tokens,
)


def test_phone_plain():
    assert normalize_phone("380671234567") == (True, "380671234567")


def test_phone_formatted():
    assert normalize_phone("+38 (067) 123-45-67") == (True, "380671234567")


def test_phone_rejects():
    assert normalize_phone("0671234567")[0] is False
    assert normalize_phone("79161234567")[0] is False
    assert normalize_phone("телефон")[0] is False
    assert normalize_phone("")[0] is False
    assert normalize_phone("3806712345678")[0] is False  # 13 цифр
    assert normalize_phone("x" * 100)[0] is False  # задовгий сирий рядок


def test_short_name():
    assert format_short_name("Петренко Іван Миколайович") == "Петренко І."
    assert format_short_name("Петренко") == "Петренко"
    assert format_short_name("Коваль Анна-Марія") == "Коваль А."
    assert format_short_name("") == ""


def test_ban_reason_length():
    assert ban_reason("123456789") is None            # 9 символів — мало
    assert ban_reason("спам у чаті") == "спам у чаті"  # 11 — ок
    assert ban_reason("x" * 501) is None               # задовга


def test_ban_reason_rejects_filler():
    assert ban_reason("++++++++++") is None
    assert ban_reason("аааааааааааа") is None
    assert ban_reason("+-+-+-+-+-+-") is None


def test_ban_reason_normalizes_whitespace():
    assert ban_reason("  спам   у\n чаті  ") == "спам у чаті"
    # пробілами до 10 символів не дотягнути
    assert ban_reason("аб   вг    д") is None


def test_parse_member_tokens_formats():
    assert parse_member_tokens("@vasya") == ["vasya"]
    assert parse_member_tokens("vasya, @petya;  123456") == ["vasya", "petya", "123456"]
    assert parse_member_tokens("@a\n@b\n@c") == ["a", "b", "c"]
    assert parse_member_tokens("@a @b @c") == ["a", "b", "c"]


def test_parse_member_tokens_dedupes_case_insensitive():
    assert parse_member_tokens("@Vasya, vasya, @VASYA") == ["Vasya"]


def test_parse_member_tokens_empty():
    assert parse_member_tokens("") == []
    assert parse_member_tokens(" , ;\n ") == []


def test_urls():
    assert has_url("хочу оце https://rozetka.com.ua/item") is True
    assert has_url("без посилань") is False
    assert has_forbidden_scheme("javascript://alert(1)") is True
    assert has_forbidden_scheme("tg://resolve?domain=x") is True
    assert has_forbidden_scheme("https://ok.com і текст") is False
    assert has_forbidden_scheme("звичайний текст 10:30") is False
