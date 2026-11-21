"""TTSProviderBase._get_segment_text must not cut sentences inside quotes."""
import pytest

from core.providers.tts.base import TTSProviderBase


# Minimal stub: it only needs to satisfy the abstract-method instantiation requirement; object.__new__ bypasses
# the abstractmethod check, so a new abstract method in the base class will not fail here automatically, which is expected (the tests never call it)
class _TTS(TTSProviderBase):
    async def text_to_speak(self, text, output_file):
        return None


def _segments(text, step, first_sentence=False):
    tts = object.__new__(_TTS)
    tts.punctuations = ("。", "？", "?", "！", "!", "；", ";", "：")
    tts.first_sentence_punctuations = ("，", "~", "、", ",") + tts.punctuations
    tts.tts_text_buff, tts.processed_chars = [], 0
    tts.is_first_sentence, tts.tts_stop_request = first_sentence, False
    out = []
    for i in range(0, len(text), step):
        tts.tts_text_buff.append(text[i:i + step])
        segment = tts._get_segment_text()
        if segment:
            out.append(segment)
    tts.tts_stop_request = True
    rest = tts._get_segment_text()
    if rest:
        out.append(rest)
    return out


@pytest.mark.parametrize("step", [1, 3, 7])
def test_japanese_quote_followed_by_tte_stays_whole(step):
    assert _segments("思わず「わぁ！」って声が出たんだ。きみは？", step) == [
        "思わず「わぁ！」って声が出たんだ",
        "きみは？",
    ]


@pytest.mark.parametrize("step", [1, 4])
def test_chinese_quote_followed_by_speaker_stays_whole(step):
    segments = _segments("他大声说：“好！”然后笑了。", step)
    assert not any(seg.startswith("”") for seg in segments)
    # the leading “ is stripped by textUtils, the closing ” must stay with its sentence
    assert any("好！”然后笑了" in seg for seg in segments)


@pytest.mark.parametrize("step", [1, 5])
def test_closing_quote_then_period_is_not_left_alone(step):
    assert _segments("「すごい！」。次はね、雨！", step) == ["「すごい！」", "次はね、雨"]


@pytest.mark.parametrize("step", [1, 5])
def test_text_without_quotes_is_split_as_before(step):
    assert _segments("普通の文。次の文！最後", step) == ["普通の文", "次の文", "最後"]


@pytest.mark.parametrize("step", [1, 4])
def test_ascii_quote_at_stream_end_stays_whole(step):
    # The old logic cut at ！ and left the orphaned " at the start of the next segment; now it waits for the quote to close and emits the whole sentence at once
    assert _segments('She said, "Hi!"', step) == ['She said, "Hi!"']


_LQ, _RQ = "\u00ab", "\u00bb"        # French/Russian guillemet-style quotes
_GOPEN, _GCLOSE = "\u201e", "\u201c"  # German low quotation marks


@pytest.mark.parametrize("step", [1, 4])
def test_guillemets_are_not_split(step):
    # ！ is inside « »; wait for the closing guillemet, then split at the sentence-final punctuation
    text = "Il a dit " + _LQ + "Bonjour ! " + _RQ + " puis est parti."
    assert _segments(text, step) == [text]


@pytest.mark.parametrize("step", [1, 4])
def test_german_low9_quotes_stay_whole(step):
    # The closing mark “ of „…“ is the opening quote of Chinese quotes: “ is always counted as an opener by the (“”, ””) pair,
    # so after „…“ closes it is still judged unbalanced, whether or not sentence-final punctuation follows (measured),
    # so German can only flush the whole sentence on stop - a conservative expectation under the character clash, not a defect
    text = "Er sagte " + _GOPEN + "Hallo!" + _GCLOSE + " und ging."
    assert _segments(text, step) == [text]
    # closing + Chinese full stop also flushes the whole sentence: the normal path is unreachable because of the dual meaning of “
    text2 = "Er sagte " + _GOPEN + "Hallo!" + _GCLOSE + "。Dann geht er。"
    assert _segments(text2, step) == [text2]


@pytest.mark.parametrize("step", [1, 4])
def test_unterminated_quote_flushes_on_stop(step):
    # a single quote symbol (unclosed) must not hang: wait during streaming, flush the whole text on stop
    assert _segments('"Hi', step) == ['"Hi']
    assert _segments("「わぁ", step) == ["「わぁ"]


_LB, _RB = "\u3010", "\u3011"  # 【】


@pytest.mark.parametrize("step", [1, 4])
def test_brackets_with_punct_inside_are_not_split(step):
    # ！ inside 【】 must not be split (otherwise the first segment would stop at "他说【重要");
    # wait for the closing bracket, then split at the sentence-final punctuation
    assert _segments("他说" + _LB + "重要！步骤" + _RB + "。然后走了。", step) == [
        "他说" + _LB + "重要！步骤",
        "然后走了",
    ]


def test_nested_mixed_quotes_stay_whole():
    # nested quotes are counted independently pair by pair and do not interfere with each other
    assert _segments("“他说『好』了。”然后离开。", 1) == ["他说『好』了。”然后离开"]


def test_apostrophes_do_not_block_splitting():
    # a single quote is ambiguous with an apostrophe (don't) and must not enter the balance check, which would delay the whole sentence
    # Note: the flush segment (the text remaining on stop) is output as is, without strip
    assert _segments("don't! It's fine.", 1) == ["don't", " It's fine."]


def test_first_sentence_still_splits_at_comma():
    assert _segments("そっか、実習で疲れたんだね。", 1, first_sentence=True)[0] == "そっか"
