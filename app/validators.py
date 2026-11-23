"""Validator teks yang dipakai bersama oleh beberapa halaman."""


def count_words(text):
    """Jumlah kata: potongan teks yang dipisahkan spasi atau baris baru."""
    return len((text or "").split())