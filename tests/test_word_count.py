from app.validators import count_words


def test_count_words():
    assert count_words("") == 0
    assert count_words(None) == 0
    assert count_words("  satu   dua\ntiga  ") == 3
    assert count_words("website company profile dan desain logo") == 6