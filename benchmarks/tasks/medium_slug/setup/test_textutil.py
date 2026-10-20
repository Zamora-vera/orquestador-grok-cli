from textutil import slugify


def test_basic():
    assert slugify('Hola Mundo') == 'hola-mundo'


def test_spaces_and_punctuation():
    assert slugify('  Hola__Mundo!! ') == 'hola-mundo'


def test_accents():
    assert slugify('Café Ñandú') == 'cafe-nandu'


def test_collapse():
    assert slugify('a---b') == 'a-b'


def test_empty():
    assert slugify('') == ''
