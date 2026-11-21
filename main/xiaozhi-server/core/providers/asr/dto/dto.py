from enum import Enum
from typing import Union, Optional


class InterfaceType(Enum):
    # interface type
    STREAM = "STREAM"  #> streaming interface
    NON_STREAM = "NON_STREAM"  #> non-streaming interface
    LOCAL = "LOCAL"  #> local service
