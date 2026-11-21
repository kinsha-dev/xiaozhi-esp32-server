from enum import Enum
from typing import Union, Optional


class SentenceType(Enum):
    # speaking phase
    FIRST = "FIRST"  #> first sentence
    MIDDLE = "MIDDLE"  #> in the middle of speaking
    LAST = "LAST"  #> last sentence


class ContentType(Enum):
    # content type
    TEXT = "TEXT"  #> text content
    FILE = "FILE"  #> file content
    ACTION = "ACTION"  #> action content


class InterfaceType(Enum):
    # interface type
    DUAL_STREAM = "DUAL_STREAM"  #> dual streaming
    SINGLE_STREAM = "SINGLE_STREAM"  #> single streaming
    NON_STREAM = "NON_STREAM"  #> non-streaming


class TTSMessageDTO:
    def __init__(
        self,
        sentence_id: str,
        # speaking phase
        sentence_type: SentenceType,
        # content type
        content_type: ContentType,
        # content details, usually the text to convert or the lyrics of the audio
        content_detail: Optional[str] = None,
        # if the content type is file, the file path must be passed in
        content_file: Optional[str] = None,
    ):
        self.sentence_id = sentence_id
        self.sentence_type = sentence_type
        self.content_type = content_type
        self.content_detail = content_detail
        self.content_file = content_file
