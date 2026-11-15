from core.utils.ignore_gate import IgnoreGate


def run(chunks):
    g = IgnoreGate()
    out = "".join(g.feed(c) for c in chunks) + g.flush()
    return out, g.ignoring


def matched(chunks):
    g = IgnoreGate()
    "".join(g.feed(c) for c in chunks)
    g.flush()
    return g.matched


def test_marker_is_dropped_even_when_split_across_chunks():
    assert run(["[ign", "ore]"]) == ("", True)
    assert run(["[ignore]"]) == ("", True)
    assert run([" [", "ignore", "]."]) == ("", True)


def test_normal_replies_pass_through_whole():
    assert run(["Hello", " there"]) == ("Hello there", False)
    assert run(["[note] hi"]) == ("[note] hi", False)


def test_short_undecided_reply_is_released_at_the_end():
    assert run(["[ig"]) == ("[ig", False)


def test_ack_marker_is_dropped_even_when_split_across_chunks():
    assert run(["[ac", "k]"]) == ("", False)  # not "ignoring": a different marker
    assert matched(["[ac", "k]"]) == "ack"
    assert matched(["[ack]"]) == "ack"


def test_bare_ack_with_only_punctuation_is_still_a_tone():
    assert run(["[ack]."]) == ("", False)
    assert matched(["[ack]."]) == "ack"
    assert run(["  [ack]  "]) == ("", False)
    assert matched(["[ack] "]) == "ack"


def test_ack_with_a_real_reply_is_spoken_without_the_marker():
    # "[ack] Done, timer set" must not swallow the words. Only a bare [ack] is a tone.
    assert run(["[ack] Done, timer set"]) == ("Done, timer set", False)
    assert matched(["[ack] Done, timer set"]) is None
    assert run(["[ack]", " Done, timer set"]) == ("Done, timer set", False)
    assert run(["[ac", "k] Done"]) == ("Done", False)
    assert run(["[ack]. ", "Done"]) == ("Done", False)


def test_ignore_still_drops_anything_after_the_marker():
    assert run(["[ignore] hello"]) == ("", True)
    assert matched(["[ignore] hello"]) == "ignore"


def test_ignore_and_ack_markers_do_not_interfere():
    assert matched(["[ignore]"]) == "ignore"
    assert matched(["Okay."]) is None  # ordinary text: passed through, not a marker
