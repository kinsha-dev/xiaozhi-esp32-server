import importlib
import pkgutil
from config.logger import setup_logging

TAG = __name__

logger = setup_logging()

def auto_import_modules(package_name):
    """
    Automatically import all modules inside the given package.

    Args:
        package_name (str): the package name, such as 'functions'.
    """
    # get the path of the package
    package = importlib.import_module(package_name)
    package_path = package.__path__

    # iterate over all modules in the package
    for _, module_name, _ in pkgutil.iter_modules(package_path):
        # import the module
        full_module_name = f"{package_name}.{module_name}"
        importlib.import_module(full_module_name)
        # logger.bind(tag=TAG).info(f"Module '{full_module_name}' loaded")