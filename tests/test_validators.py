from app.services.validators import (
    format_short_name,
    has_forbidden_scheme,
    has_url,
    normalize_phone,
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


def test_urls():
    assert has_url("хочу оце https://rozetka.com.ua/item") is True
    assert has_url("без посилань") is False
    assert has_forbidden_scheme("javascript://alert(1)") is True
    assert has_forbidden_scheme("tg://resolve?domain=x") is True
    assert has_forbidden_scheme("https://ok.com і текст") is False
    assert has_forbidden_scheme("звичайний текст 10:30") is False
