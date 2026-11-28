"""Reply offers every recipient the composer has, and each one gets what it can use."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = (Path(__file__).parents[1] / "ctrl_ui" / "index.html").read_text()


def _values(text):
    return re.findall(r'\{label:"[^"]+",value:"([a-z]+)"\}', text)


def test_reply_offers_the_same_recipients_as_the_composer():
    composer = _values(next(x for x in HTML.splitlines() if 'id:"to",component:"ChoicePicker"' in x and 'label:"To"' in x))
    reply = _values(next(x for x in HTML.splitlines() if 'label:"Reply to",value:{"@path":"/reply/to"}' in x))
    assert reply == composer and "note" in reply


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_what_each_recipient_gets():
    reply = HTML[HTML.index(" compose(t,text){"):HTML.index(" async send(ctx){")]
    script = ('const QUOTE_MAX=1000,CHAT_MAX=4000,R={' + reply + '};'
              'const t={who:"Claude",time:"now",quote:"First line\\n\\nSee https://x.test/a."};'
              'const out={};for(const to of ["vibi","claude","grok","gemini","siri","computer","todo","note"])'
              'out[to]=[R.body(to,t,"do it"),R.body(to,t,"")];console.log(JSON.stringify(out))')
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for to in ("claude", "grok", "gemini"):
        assert out[to][0].startswith("Replying to Claude (now):") and out[to][0].endswith("do it") and not out[to][1]
    for to in ("vibi", "siri"):
        assert out[to] == ["do it", ""]
    assert out["computer"] == ["do it (link: https://x.test/a)", None]
    assert out["todo"] == ["do it (link: https://x.test/a)", "First line"]
    assert out["note"] == ["do it\n> First line\n> See https://x.test/a.", "First line\nSee https://x.test/a."]
