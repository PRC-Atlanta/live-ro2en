from srt_set import _votes, keys, parse_srt


def test_parse_srt_joins_lines_and_reads_times(tmp_path):
    p = tmp_path / "x_ro.srt"
    p.write_text("1\n00:00:00,136 --> 00:00:05,756\nCum îți dai\nseama\n\n"
                 "2\n00:01:02,500 --> 00:01:03,000\npentru căsătorie?\n", encoding="utf-8")
    cues = parse_srt(p)
    assert [c["text"] for c in cues] == ["Cum îți dai seama", "pentru căsătorie?"]
    assert cues[0]["start"] == 0.136 and cues[1]["start"] == 62.5


def test_hyphenated_article_is_one_word():
    assert keys("la podcast-ul nostru") == keys("la podcastul nostru")
    assert keys("podcast-urile trecute") == keys("podcasturile trecute")
    assert len(keys("într-o zi")) == 3          # other hyphens still split


def test_votes_substitution_deletion_insertion():
    v = _votes("a b c d e f".split(), "a x c e f g".split())
    assert v == {("sub", 1): "x", ("sub", 3): "", ("ins", 6): "g"}
