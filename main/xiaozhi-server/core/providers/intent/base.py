from abc import ABC, abstractmethod
from typing import List, Dict
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class IntentProviderBase(ABC):
    def __init__(self, config):
        self.config = config

    def set_llm(self, llm):
        self.llm = llm
        # get the model name and type information
        model_name = getattr(llm, "model_name", str(llm.__class__.__name__))
        # log more detail
        logger.bind(tag=TAG).info(f"Intent recognition set LLM: {model_name}")

    @abstractmethod
    async def detect_intent(self, conn, dialogue_history: List[Dict], text: str) -> str:
        """
        Detect the intent of the user's last sentence
        Args:
            dialogue_history: list of dialogue history records, each containing role and content
        Returns:
            Returns the recognized intent in the format:
            - "continue chat"
            - "end chat"
            - "play music song name" or "play random music"
            - "query weather place name" or "query weather [current location]"
        """
        pass
