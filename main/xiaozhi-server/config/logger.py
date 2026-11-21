import os
import sys
import asyncio
from loguru import logger
from config.config_loader import load_config
from config.settings import check_config_file
from core.utils.cache.manager import cache_manager, CacheType

SERVER_VERSION = "0.9.8"
_logger_initialized = False


def get_module_abbreviation(module_name, module_dict):
    """Get the abbreviation of the module name, or 00 if it is empty
    If the name contains an underscore, return the first two characters after the underscore
    """
    module_value = module_dict.get(module_name, "")
    if not module_value:
        return "00"
    if "_" in module_value:
        parts = module_value.split("_")
        return parts[-1][:2] if parts[-1] else "00"
    return module_value[:2]


def build_module_string(selected_module):
    """Build the module string"""
    return (
        get_module_abbreviation("VAD", selected_module)
        + get_module_abbreviation("ASR", selected_module)
        + get_module_abbreviation("LLM", selected_module)
        + get_module_abbreviation("TTS", selected_module)
        + get_module_abbreviation("Memory", selected_module)
        + get_module_abbreviation("Intent", selected_module)
        + get_module_abbreviation("VLLM", selected_module)
    )


def formatter(record):
    """Add a default value to logs without a tag and handle the dynamic module string"""
    record["extra"].setdefault("tag", record["name"])
    # if selected_module is not set, use the default value
    record["extra"].setdefault("selected_module", "00000000000000")
    # extract selected_module from extra to the top level, to support the {selected_module} format
    record["selected_module"] = record["extra"]["selected_module"]
    return record["message"]


def setup_logging(config=None):
    """Read the log configuration from the config file and set the log output format and level"""
    if config is None:
        check_config_file()
        # check the cache first, to avoid awaiting load_config repeatedly in an async context
        config = cache_manager.get(CacheType.CONFIG, "main_config")
        if config is None:
        # if the cache has nothing either (should not happen in theory), fall back to asyncio.run
            config = asyncio.run(load_config())
    log_config = config["log"]
    global _logger_initialized

    # configure logging on first initialization
    if not _logger_initialized:
        # use the default module string for initialization
        logger.configure(
            extra={
                "selected_module": log_config.get("selected_module", "00000000000000"),
            }
        )

        log_format = log_config.get(
            "log_format",
            "<green>{time:YYMMDD HH:mm:ss}</green>[{version}_{extra[selected_module]}][<light-blue>{extra[tag]}</light-blue>]-<level>{level}</level>-<light-green>{message}</light-green>",
        )
        log_format_file = log_config.get(
            "log_format_file",
            "{time:YYYY-MM-DD HH:mm:ss} - {version}_{extra[selected_module]} - {name} - {level} - {extra[tag]} - {message}",
        )
        log_format = log_format.replace("{version}", SERVER_VERSION)
        log_format_file = log_format_file.replace("{version}", SERVER_VERSION)

        log_level = log_config.get("log_level", "INFO")
        log_dir = log_config.get("log_dir", "tmp")
        log_file = log_config.get("log_file", "server.log")
        data_dir = log_config.get("data_dir", "data")

        os.makedirs(log_dir, exist_ok=True)
        os.makedirs(data_dir, exist_ok=True)

        # configure log output
        logger.remove()

        # output to the console
        logger.add(sys.stdout, format=log_format, level=log_level, filter=formatter)

        # output to a file - unified directory, rotated by size
        # log file path
        log_file_path = os.path.join(log_dir, log_file)

        # add the log handlers
        logger.add(
            log_file_path,
            format=log_format_file,
            level=log_level,
            filter=formatter,
            rotation="10 MB",  # each file at most 10MB
            retention="30 days",  # keep for 30 days
            compression=None,
            encoding="utf-8",
            enqueue=True,  # async safe
            backtrace=True,
            diagnose=True,
        )
        _logger_initialized = True  # mark as initialized

    return logger


def create_connection_logger(selected_module_str):
    """Create an independent logger for a connection, bound to a specific module string"""
    return logger.bind(selected_module=selected_module_str)
