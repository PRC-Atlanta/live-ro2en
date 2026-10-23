from textnorm import cyr_to_latin, key_tokens, phon_key, strip_headers, tokens, fix_cedilla


def test_translit_basic():
    assert cyr_to_latin("Ла ынчепут, Думнезеу а фэкут черуриле ши пэмынтул.") == \
        "La început, Dumnezeu a făcut cerurile și pământul."


def test_translit_velars_and_palatals():
    assert cyr_to_latin("окь") == "ochi"
    assert cyr_to_latin("ынкиде") == "închide"
    assert cyr_to_latin("гиндэ") == "ghindă"
    assert cyr_to_latin("чинч") == "cinci"
    assert cyr_to_latin("ачаста") == "aceasta"
    assert cyr_to_latin("дясупра") == "deasupra"
    assert cyr_to_latin("крединчошия") == "credincioșia"
    assert cyr_to_latin("нелеӂюиря") == "nelegiuirea"
    assert cyr_to_latin("ынчюдат") == "înciudat"


def test_phon_key_merges_script_variants():
    assert phon_key("cuvântul") == phon_key("cuvîntul")
    assert phon_key("deasupra") == phon_key("diasupra")
    assert phon_key("sînt") == phon_key("sunt")
    assert phon_key("ochi") == phon_key("och")
    assert phon_key("casă") != phon_key("casa")      # article difference stays an error


def test_cedilla_and_hyphen_tokens():
    assert fix_cedilla("şi ţară") == "și țară"
    assert tokens("într-o zi, Isus") == ["într", "o", "zi", "isus"]


def test_strip_headers():
    k = key_tokens("Cartea Geneza, capitolul 1. La început, Dumnezeu a făcut")
    assert strip_headers(k) == key_tokens("La început, Dumnezeu a făcut")
    k = key_tokens("cerurile. Capitolul douăzeci și trei. Și a zis")
    assert strip_headers(k) == key_tokens("cerurile. Și a zis")
    k = key_tokens("Psalmul 23 Domnul este Păstorul meu")
    assert strip_headers(k) == key_tokens("Domnul este Păstorul meu")


def test_iisus_is_a_spelling_variant():
    assert phon_key("iisus") == phon_key("isus")
    from review_packet import diff_spans
    assert all(l is None for _, l in diff_spans("Domnul Isus Hristos", "Domnul Iisus Hristos"))


def test_preferred_spellings():
    from textnorm import apply_spellings
    sp = {"Filadelfia": "Philadelphia", "Iisus": "Isus"}
    assert apply_spellings("Biserica Filadelfia și Domnul Iisus.", sp) == "Biserica Philadelphia și Domnul Isus."
    assert apply_spellings("biserica filadelfia, nu Filadelfiana", sp) == "biserica philadelphia, nu Filadelfiana"
    assert phon_key("filadelfia") == phon_key("philadelphia")
