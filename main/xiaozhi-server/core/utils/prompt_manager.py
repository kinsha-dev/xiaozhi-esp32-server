"""
System prompt manager module
Manages and updates the system prompt, including quick initialization and asynchronous enhancement
"""

import os
import asyncio
import hashlib
import threading
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from config.logger import setup_logging
from jinja2 import Template

TAG = __name__

WEEKDAY_MAP = {
    "Monday": "Monday",
    "Tuesday": "Tuesday",
    "Wednesday": "Wednesday",
    "Thursday": "Thursday",
    "Friday": "Friday",
    "Saturday": "Saturday",
    "Sunday": "Sunday",
}

EMOJI_List = [
    "😶",
    "🙂",
    "😆",
    "😂",
    "😔",
    "😠",
    "😭",
    "😍",
    "😳",
    "😲",
    "😱",
    "🤔",
    "😉",
    "😎",
    "😌",
    "🤤",
    "😘",
    "😏",
    "😴",
    "😜",
    "🙄",
]


class PromptManager:
    """System prompt manager, manages and updates the system prompt"""

    def __init__(self, config: Dict[str, Any], logger=None):
        self.config = config
        self.logger = logger or setup_logging()
        self.base_prompt_template = None
        self.last_update_time = 0

        # import the global cache manager
        from core.utils.cache.manager import cache_manager, CacheType

        self.cache_manager = cache_manager
        self.CacheType = CacheType

        # initialize the context sources
        from core.utils.context_provider import ContextDataProvider

        self.context_provider = ContextDataProvider(config, self.logger)
        self.context_data = {}

        self._load_base_template()

    def _load_base_template(self):
        """Load the base prompt template"""
        try:
            template_path = self.config.get("prompt_template", None)
            if not template_path:
                template_path = "agent-base-prompt.txt"
            cache_key = f"prompt_template:{template_path}"

            # try the cache first
            cached_template = self.cache_manager.get(self.CacheType.CONFIG, cache_key)
            if cached_template is not None:
                self.base_prompt_template = cached_template
                self.logger.bind(tag=TAG).debug("Loaded the base prompt template from cache")
                return

            # cache miss, read from file
            if os.path.exists(template_path):
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()

                # store in the cache (CONFIG type does not expire automatically; invalidate manually)
                self.cache_manager.set(
                    self.CacheType.CONFIG, cache_key, template_content
                )
                self.base_prompt_template = template_content
                self.logger.bind(tag=TAG).debug("Loaded the base prompt template and cached it")
            else:
                self.logger.bind(tag=TAG).warning(f"File {template_path} not found")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to load the prompt template: {e}")

    def get_quick_prompt(self, user_prompt: str, device_id: str = None) -> str:
        """Quickly get the system prompt (using the user configuration)"""
        device_cache_key = f"device_prompt:{device_id}"
        cached_device_prompt = self.cache_manager.get(
            self.CacheType.DEVICE_PROMPT, device_cache_key
        )
        if cached_device_prompt is not None:
            self.logger.bind(tag=TAG).debug(f"Using the cached prompt of device {device_id}")
            return cached_device_prompt
        else:
            self.logger.bind(tag=TAG).debug(
                f"Device {device_id} has no cached prompt, using the supplied prompt"
            )

        # use the supplied prompt and cache it (if there is a device ID)
        if device_id:
            self.cache_manager.set(
                self.CacheType.DEVICE_PROMPT, device_cache_key, user_prompt
            )
            self.logger.bind(tag=TAG).debug(f"The prompt of device {device_id} has been cached")

        self.logger.bind(tag=TAG).info(f"Using the quick prompt: {user_prompt[:50]}...")
        return user_prompt

    def _get_current_time_info(self) -> tuple:
        """Get the current time information"""
        from .current_time import (
            get_current_date,
            get_current_weekday,
        )

        today_date = get_current_date()
        today_weekday = get_current_weekday()

        return today_date, today_weekday

    def _get_location_info(self, client_ip: str) -> str:
        """Get location information"""
        try:
            # try the cache first
            cached_location = self.cache_manager.get(self.CacheType.LOCATION, client_ip)
            if cached_location is not None:
                return cached_location

            # cache miss, fetch from the API
            from core.utils.util import get_ip_info

            ip_info = get_ip_info(client_ip, self.logger)
            city = ip_info.get("city", "Unknown location")
            location = f"{city}"

            # store in the cache
            self.cache_manager.set(self.CacheType.LOCATION, client_ip, location)
            return location
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to get location information: {e}")
            return "Unknown location"

    def _get_weather_info(self, conn: "ConnectionHandler", location: str) -> str:
        """Weather lookup was removed together with the Chinese weather provider."""
        return ""

    def update_context_info(self, conn, client_ip: str):
        """Update context information synchronously"""
        try:
            local_address = ""
            if (
                client_ip
                and self.base_prompt_template
                and (
                    "local_address" in self.base_prompt_template
                    or "weather_info" in self.base_prompt_template
                )
            ):
                # get location information (using the global cache)
                local_address = self._get_location_info(client_ip)

            if (
                self.base_prompt_template
                and "weather_info" in self.base_prompt_template
                and local_address
            ):
                # get weather information (using the global cache)
                self._get_weather_info(conn, local_address)

            # get the configured context data
            if hasattr(conn, "device_id") and conn.device_id:
                if (
                    self.base_prompt_template
                    and "dynamic_context" in self.base_prompt_template
                ):
                    self.context_data = self.context_provider.fetch_all(conn.device_id)
                else:
                    self.context_data = ""

            self.logger.bind(tag=TAG).debug(f"Context information updated")

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to update context information: {e}")

    def build_enhanced_prompt(
        self, user_prompt: str, device_id: str, client_ip: str = None, *args, **kwargs
    ) -> str:
        """Build the enhanced system prompt"""
        if not self.base_prompt_template:
            return user_prompt

        # the cache key must cover all rendering inputs: the emoji switch and device prompt changes would otherwise hit a stale prompt
        emoji_enabled = kwargs.get("emoji_enabled", True)
        prompt_hash = hashlib.md5(user_prompt.encode("utf-8")).hexdigest()[:8]
        enhanced_cache_key = (
            f"enhanced_prompt:{device_id}:{emoji_enabled}:{prompt_hash}"
        )
        cached_prompt = self.cache_manager.get(
            self.CacheType.DEVICE_PROMPT, enhanced_cache_key
        )
        if cached_prompt is not None:
            return cached_prompt

        try:
            # get the latest time information (not cached)
            today_date, today_weekday = self._get_current_time_info()

            # get the cached context information
            local_address = ""
            weather_info = ""

            if client_ip:
                # get location information (from the global cache)
                local_address = (
                    self.cache_manager.get(self.CacheType.LOCATION, client_ip) or ""
                )

                # get weather information (from the global cache)
                if local_address:
                    weather_info = (
                        self.cache_manager.get(self.CacheType.WEATHER, local_address)
                        or ""
                    )

            # get the language selected for TTS, default is English
            language = (
                self.config.get("TTS", {})
                .get(self.config.get("selected_module", {}).get("TTS", ""), {})
                .get("language")
                or "English"
            )
            self.logger.bind(tag=TAG).debug(f"Selected language: {language}")

            # replace template variables
            template = Template(self.base_prompt_template)
            enhanced_prompt = template.render(
                base_prompt=user_prompt,
                current_time="{{current_time}}",
                today_date=today_date,
                today_weekday=today_weekday,
                local_address=local_address,
                weather_info=weather_info,
                emojiList=EMOJI_List,
                device_id=device_id,
                client_ip=client_ip,
                dynamic_context=self.context_data,
                language=language,
                *args,
                **kwargs,
            )
            self.cache_manager.set(
                self.CacheType.DEVICE_PROMPT,
                enhanced_cache_key,
                enhanced_prompt,
                ttl=300,
            )
            self.logger.bind(tag=TAG).debug(
                f"Enhanced prompt built successfully, length: {len(enhanced_prompt)}"
            )
            return enhanced_prompt

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to build the enhanced prompt: {e}")
            return user_prompt
