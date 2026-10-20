from security_sql import quote_literal


def test_quote():
    assert quote_literal("O'Brien") == "'O''Brien'"


def test_injection():
    assert quote_literal("' OR 1=1 --") == "''' OR 1=1 --'"
