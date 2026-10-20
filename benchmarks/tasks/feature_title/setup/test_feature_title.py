from title import title_case


def test_title():
    assert title_case("hola MUNDO") == "Hola Mundo"
    assert title_case("") == ""
