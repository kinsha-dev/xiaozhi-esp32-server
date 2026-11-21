import os
import asyncio
from config.config_loader import read_config, get_project_dir, load_config


default_config_file = "config.yaml"
config_file_valid = False


def check_config_file():
    global config_file_valid
    if config_file_valid:
        return
    """
    Simplified configuration check; only tells the user how the configuration files are used
    """
    custom_config_file = get_project_dir() + "data/." + default_config_file
    if not os.path.exists(custom_config_file):
        raise FileNotFoundError(
            "data/.config.yaml not found, please check the setup guide to confirm that this configuration file exists"
        )

    config = asyncio.run(load_config())
    config_file_valid = True
