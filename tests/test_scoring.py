from scoring import align, score, corpus_score


def test_exact_match_inside_long_reference():
    ref = "a b c d e f g h i j".split()
    s = score(ref, "d e f".split())
    assert s["wer"] == 0 and s["ref_start"] == 3 and s["ref_end"] == 6


def test_counts_sub_del_ins():
    ref = "x x the cat sat on the mat y y".split()
    a = align(ref, "the bat sat the mat extra".split())
    assert (a.sub, a.dele, a.ins) == (1, 1, 1)
    assert a.n_ref == 6


def test_no_free_ends_counts_all_reference():
    a = align("a b c".split(), "b".split(), free_ends=False)
    assert a.dele == 2 and a.wer == 2 / 3


def test_long_reference_window_path():
    ref = [f"w{i}" for i in range(20000)]
    hyp = ref[15000:15050]
    s = score(ref, hyp)
    assert s["wer"] == 0 and s["ref_start"] == 15000


def test_corpus_pooling():
    items = [score("a b c d".split(), "a b c d".split(), False),
             score("a b".split(), "a x".split(), False)]
    assert abs(corpus_score(items)["wer"] - 1 / 6) < 1e-9


def test_rover_majority():
    from scoring import rover
    h = [["a", "b", "c", "d"], ["a", "x", "c", "d"], ["a", "b", "c"], ["a", "b", "c", "d", "e"]]
    assert rover(h) == ["a", "b", "c", "d"]
    assert rover([["a"], []]) == ["a"]
