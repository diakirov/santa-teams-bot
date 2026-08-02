from app.services.invites import ALPHABET, CODE_LENGTH, generate_code, looks_like_code


def test_code_shape():
    for _ in range(200):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert all(ch in ALPHABET for ch in code)


def test_no_ambiguous_chars():
    assert not set("0O1Il") & set(ALPHABET)


def test_looks_like_code():
    assert looks_like_code(generate_code()) is True
    assert looks_like_code(generate_code().lower()) is True  # регістр не важливий
    assert looks_like_code("short") is False
    assert looks_like_code("ЩОСЬІНШЕ") is False
    assert looks_like_code("ABCD0123") is False  # заборонені символи
