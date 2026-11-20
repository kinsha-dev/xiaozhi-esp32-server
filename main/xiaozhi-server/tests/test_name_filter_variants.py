from core.utils.name_filter import says_name


def test_heard_variants_pass():
    for t in ["Hi, we can you try opening iPhone mirroring", "Hi, we'll be open iPhone", "hey we open the news", "Hi Vibhi, give a summary", "hi vi bi"]:
        assert says_name(t), t


def test_ordinary_speech_still_dropped():
    for t in ["we can go now", "what do we have today", "the tv is on", "Hi there, how are you"]:
        assert not says_name(t), t


def test_bb_pb_variants():
    from core.utils.name_filter import says_name
    assert says_name("Hi Bb, play the latest English news on the TV.")
    assert says_name("Hi, Pb. Play latest news on the TV.")
    assert not says_name("Hi there, how are you")
