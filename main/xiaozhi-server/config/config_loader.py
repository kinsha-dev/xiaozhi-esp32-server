import os
import asyncio
import yaml
from collections.abc import Mapping


def get_project_dir():
    """Get the project root directory"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


async def load_config():
    """Load the configuration file"""
    from core.utils.cache.manager import cache_manager, CacheType

    # check the cache
    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config

    default_config_path = get_project_dir() + "config.yaml"
    custom_config_path = get_project_dir() + "data/.config.yaml"

    # load the default configuration
    default_config = read_config(default_config_path)
    custom_config = read_config(custom_config_path)

    config = merge_configs(default_config, custom_config)
    # initialize the directories
    ensure_directories(config)

    # cache the configuration
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


def ensure_directories(config):
    """Make sure all configured paths exist"""
    dirs_to_create = set()
    project_dir = get_project_dir()  # get the project root directory
    # log file directory
    log_dir = config.get("log", {}).get("log_dir", "tmp")
    dirs_to_create.add(os.path.join(project_dir, log_dir))

    # ASR/TTS module output directories
    for module in ["ASR", "TTS"]:
        if config.get(module) is None:
            continue
        for provider in config.get(module, {}).values():
            output_dir = provider.get("output_dir", "")
            if output_dir:
                dirs_to_create.add(output_dir)

    # create the model directories according to selected_module
    selected_modules = config.get("selected_module", {})
    for module_type in ["ASR", "LLM", "TTS"]:
        selected_provider = selected_modules.get(module_type)
        if not selected_provider:
            continue
        if config.get(module_type) is None:
            continue
        if config.get(selected_provider) is None:
            continue
        provider_config = config.get(module_type, {}).get(selected_provider, {})
        output_dir = provider_config.get("output_dir")
        if output_dir:
            full_model_dir = os.path.join(project_dir, output_dir)
            dirs_to_create.add(full_model_dir)

    # create the directories in one place (keeping the original data directory creation)
    for dir_path in dirs_to_create:
        try:
            os.makedirs(dir_path, exist_ok=True)
        except PermissionError:
            print(f"Warning: unable to create directory {dir_path}, please check write permissions")


def merge_configs(default_config, custom_config):
    """
    Recursively merge configurations; custom_config has higher priority

    Args:
        default_config: default configuration
        custom_config: user-defined configuration

    Returns:
        merged configuration
    """
    if not isinstance(default_config, Mapping) or not isinstance(
        custom_config, Mapping
    ):
        return custom_config

    merged = dict(default_config)

    for key, value in custom_config.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value

    return merged
