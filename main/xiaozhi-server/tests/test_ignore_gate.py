from core.utils.ignore_gate import IgnoreGate


def run(chunks):
    g = IgnoreGate()
    out = "".join(g.feed(c) for c in chunks) + g.flush()
    return out, g.ignoring


def test_marker_is_dropped_even_when_split_across_chunks():
    assert run(["[ign", "ore]"]) == ("", True)
    assert run(["[ignore]"]) == ("", True)
    assert run([" [", "ignore", "]."]) == ("", True)


def test_normal_replies_pass_through_whole():
    assert run(["Hello", " there"]) == ("Hello there", False)
    assert run(["[note] hi"]) == ("[note] hi", False)


def test_short_undecided_reply_is_released_at_the_end():
    assert run(["[ig"]) == ("[ig", False)
