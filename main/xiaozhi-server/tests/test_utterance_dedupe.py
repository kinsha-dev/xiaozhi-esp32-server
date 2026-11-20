from core.utils import utterance_dedupe as d


def setup_function(_):
    d._recent.clear()


def test_second_device_same_command_is_dropped():
    assert not d.is_duplicate("Hi Vibi, give me a market summary for today", 1, now=100)
    assert d.is_duplicate("hi vibi can you give a market summary for today", 2, now=130)


def test_same_device_and_different_commands_pass():
    assert not d.is_duplicate("what is the weather", 1, now=100)
    assert not d.is_duplicate("what is the weather", 1, now=101)
    assert not d.is_duplicate("open the news tab", 2, now=102)


def test_old_command_expires():
    assert not d.is_duplicate("give me the market summary", 1, now=100)
    assert not d.is_duplicate("give me the market summary", 2, now=100 + d.WINDOW_S + 1)
