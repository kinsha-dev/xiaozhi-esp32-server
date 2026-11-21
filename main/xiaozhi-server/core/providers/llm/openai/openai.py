import httpx
import openai
from openai.types import CompletionUsage
from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.llm.base import LLMProviderBase
from urllib.parse import urlparse

TAG = __name__
logger = setup_logging()

# Platforms whose thinking mode must be disabled, with the matching request parameters
THINKING_DISABLED_DOMAINS = {
    # OpenRouter: reasoning models (e.g. DeepSeek v4.1) otherwise spend the whole max_tokens budget
    # thinking and return an empty reply, which the voice device shows as silence.
    "openrouter.ai": {"reasoning": {"enabled": False}},
}

# Per-domain model-name exemptions: models that always think reject the injected parameter
THINKING_DISABLED_MODEL_EXCLUSIONS = {}


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        # optional per-instance thinking effort: off/disabled/none/disable | low/medium/high | empty/other = domain default
        self.thinking_mode = str(config.get("thinking") or "").strip().lower()
        self.api_key = config.get("api_key")
        if "base_url" in config:
            self.base_url = config.get("base_url")
        else:
            self.base_url = config.get("url")
        
        timeout_config = config.get("timeout")
        if isinstance(timeout_config, dict):
            # fine-grained timeout configuration
            custom_timeout = httpx.Timeout(
                pool=timeout_config.get("pool", 2.0),
                connect=timeout_config.get("connect", 3.0),
                write=timeout_config.get("write", 5.0),
                read=timeout_config.get("read", 60.0)
            )
        elif isinstance(timeout_config, (int, float)) and timeout_config > 0:
            # compatible with the old single timeout configuration (int or float)
            custom_timeout = httpx.Timeout(timeout_config)
        else:
            # not configured or invalid, use the default value
            custom_timeout = httpx.Timeout(300)

        param_defaults = {
            "max_tokens": int,
            "temperature": lambda x: round(float(x), 1),
            "top_p": lambda x: round(float(x), 1),
            "frequency_penalty": lambda x: round(float(x), 1),
        }

        for param, converter in param_defaults.items():
            value = config.get(param)
            try:
                setattr(
                    self,
                    param,
                    converter(value) if value not in (None, "") else None,
                )
            except (ValueError, TypeError):
                setattr(self, param, None)

        logger.debug(
            f"Intent recognition parameter initialization: {self.temperature}, {self.max_tokens}, {self.top_p}, {self.frequency_penalty}"
        )

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=custom_timeout)

    @staticmethod
    def normalize_dialogue(dialogue):
        """Automatically repair messages in the dialogue that are missing content"""
        for msg in dialogue:
            if "role" in msg and "content" not in msg:
                msg["content"] = ""
        return dialogue

    def _apply_thinking_disabled(self, request_params: dict):
        """Thinking mode control: the instance configuration (thinking) takes priority, otherwise the per-domain default table"""
        mode = getattr(self, "thinking_mode", "")
        if mode in ("keep", "passthrough", "default"):
        # no intervention: neither send a thinking parameter nor consult the domain table (for models that always think or reject the parameter)
            return
        if mode in ("off", "disabled", "none", "disable"):
            request_params.setdefault("extra_body", {}).update({"thinking": {"type": "disabled"}})
            logger.bind(tag=TAG).info("Thinking effort=off: thinking mode disabled (instance configuration)")
            return
        if mode in ("low", "medium", "high"):
            request_params.setdefault("extra_body", {}).update(
                {"reasoning_effort": mode, "thinking": {"type": "enabled"}})
            logger.bind(tag=TAG).info(f"Thinking effort={mode}: reasoning_effort set (instance configuration)")
            return
        parsed_url = urlparse(self.base_url)
        domain = parsed_url.netloc
        model_name = (self.model_name or "").lower()
        for disabled_domain, params in THINKING_DISABLED_DOMAINS.items():
            if disabled_domain not in domain:
                continue
            exclusions = THINKING_DISABLED_MODEL_EXCLUSIONS.get(disabled_domain, ())
            if any(ex in model_name for ex in exclusions):
                logger.bind(tag=TAG).info(
                    f"Domain {domain} matched a thinking-disabled rule, but model {self.model_name} always thinks and rejects the parameter, skipping injection")
                break
            request_params.setdefault("extra_body", {}).update(params)
            logger.bind(tag=TAG).info(f"Disabled thinking mode for domain {domain}, parameters: {params}")
            break

    def response(self, session_id, dialogue, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
        }

        # add optional parameters, only when the parameter is not None
        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # disable thinking mode
        self._apply_thinking_disabled(request_params)

        responses = self.client.chat.completions.create(**request_params)

        is_active = True
        try:            
            for chunk in responses:
                try:
                    delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                    content = getattr(delta, "content", "") if delta else ""
                except IndexError:
                    content = ""
                if content:
                    if "<think>" in content:
                        is_active = False
                        content = content.split("<think>")[0]
                    if "</think>" in content:
                        is_active = True
                        content = content.split("</think>")[-1]
                    if is_active:
                        yield content
        finally:
            responses.close()

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            "tools": functions,
        }

        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # disable thinking mode
        self._apply_thinking_disabled(request_params)

        stream = self.client.chat.completions.create(**request_params)

        try:
            for chunk in stream:
                if getattr(chunk, "choices", None):
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", "")
                    tool_calls = getattr(delta, "tool_calls", None)
                    yield content, tool_calls
                elif isinstance(getattr(chunk, "usage", None), CompletionUsage):
                    usage_info = getattr(chunk, "usage", None)
                    logger.bind(tag=TAG).info(
                        f"Token usage: input {getattr(usage_info, 'prompt_tokens', 'unknown')}, "
                        f"output {getattr(usage_info, 'completion_tokens', 'unknown')}, "
                        f"total {getattr(usage_info, 'total_tokens', 'unknown')}"
                    )
        finally:
            stream.close()
