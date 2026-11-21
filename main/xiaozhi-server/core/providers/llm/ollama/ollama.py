from config.logger import setup_logging
from openai import OpenAI
import json
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.base_url = config.get("base_url", "http://localhost:11434")
        # Initialize OpenAI client with Ollama base URL
        # add v1 if it is missing
        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"

        self.client = OpenAI(
            base_url=self.base_url,
            api_key="ollama",  # Ollama doesn't need an API key but OpenAI client requires one
        )

        # check whether it is a qwen3 model
        self.is_qwen3 = self.model_name and self.model_name.lower().startswith("qwen3")

    def response(self, session_id, dialogue, **kwargs):
        # for a qwen3 model, add the /no_think instruction to the last user message
        if self.is_qwen3:
            # copy the dialogue list to avoid modifying the original dialogue
            dialogue_copy = dialogue.copy()

            # find the last user message
            for i in range(len(dialogue_copy) - 1, -1, -1):
                if dialogue_copy[i]["role"] == "user":
                    # add the /no_think instruction before the user message
                    dialogue_copy[i]["content"] = (
                        "/no_think " + dialogue_copy[i]["content"]
                    )
                    logger.bind(tag=TAG).debug(f"Adding the /no_think instruction for the qwen3 model")
                    break

            # use the modified dialogue
            dialogue = dialogue_copy

        responses = self.client.chat.completions.create(
            model=self.model_name, messages=dialogue, stream=True
        )
        is_active = True
        # used to handle tags that span chunks
        buffer = ""

        try:
            for chunk in responses:
                try:
                    delta = (
                        chunk.choices[0].delta
                        if getattr(chunk, "choices", None)
                        else None
                    )
                    content = delta.content if hasattr(delta, "content") else ""

                    if content:
                        # add the content to the buffer
                        buffer += content

                        # handle the tags in the buffer
                        while "<think>" in buffer and "</think>" in buffer:
                            # find a complete <think></think> tag and remove it
                            pre = buffer.split("<think>", 1)[0]
                            post = buffer.split("</think>", 1)[1]
                            buffer = pre + post

                        # handle the case with only an opening tag
                        if "<think>" in buffer:
                            is_active = False
                            buffer = buffer.split("<think>", 1)[0]

                        # handle the case with only a closing tag
                        if "</think>" in buffer:
                            is_active = True
                            buffer = buffer.split("</think>", 1)[1]

                        # if currently active and the buffer has content, output it
                        if is_active and buffer:
                            yield buffer
                            buffer = ""  # clear the buffer

                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing chunk: {e}")
        finally:
            responses.close()

    def response_with_functions(self, session_id, dialogue, functions=None):
        # for a qwen3 model, add the /no_think instruction to the last user message
        if self.is_qwen3:
            # copy the dialogue list to avoid modifying the original dialogue
            dialogue_copy = dialogue.copy()

            # find the last user message
            for i in range(len(dialogue_copy) - 1, -1, -1):
                if dialogue_copy[i]["role"] == "user":
                    # add the /no_think instruction before the user message
                    dialogue_copy[i]["content"] = (
                        "/no_think " + dialogue_copy[i]["content"]
                    )
                    logger.bind(tag=TAG).debug(f"Adding the /no_think instruction for the qwen3 model")
                    break

            # use the modified dialogue
            dialogue = dialogue_copy

        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=dialogue,
            stream=True,
            tools=functions,
        )

        is_active = True
        buffer = ""

        try:
            for chunk in stream:
                try:
                    delta = (
                        chunk.choices[0].delta
                        if getattr(chunk, "choices", None)
                        else None
                    )
                    content = delta.content if hasattr(delta, "content") else None
                    tool_calls = (
                        delta.tool_calls if hasattr(delta, "tool_calls") else None
                    )

                    # for a tool call, pass it through directly
                    if tool_calls:
                        yield None, tool_calls
                        continue

                    # handle text content
                    if content:
                        # add the content to the buffer
                        buffer += content

                        # handle the tags in the buffer
                        while "<think>" in buffer and "</think>" in buffer:
                            # find a complete <think></think> tag and remove it
                            pre = buffer.split("<think>", 1)[0]
                            post = buffer.split("</think>", 1)[1]
                            buffer = pre + post

                        # handle the case with only an opening tag
                        if "<think>" in buffer:
                            is_active = False
                            buffer = buffer.split("<think>", 1)[0]

                        # handle the case with only a closing tag
                        if "</think>" in buffer:
                            is_active = True
                            buffer = buffer.split("</think>", 1)[1]

                        # if currently active and the buffer has content, output it
                        if is_active and buffer:
                            yield buffer, None
                            buffer = ""  # clear the buffer
                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing function chunk: {e}")
                    continue
        finally:
            stream.close()
