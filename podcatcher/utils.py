from pathlib import Path

from appdirs import user_data_dir

APP_NAME = "podcatcher"
APP_AUTHOR = "Dobatymo"

DEFAULT_APPDATA_DIR = Path(user_data_dir(APP_NAME, APP_AUTHOR))
