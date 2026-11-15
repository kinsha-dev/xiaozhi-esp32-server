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


def test_ignore_and_ack_markers_do_not_interfere():
    assert matched(["[ignore]"]) == "ignore"
    assert matched(["Okay."]) is None  # ordinary text: passed through, not a marker
