import logging
import os

#------Defining a Directory Name Where all log files will be stored--------
directory_name = "logs"
os.makedirs(directory_name, exist_ok=True)
file_path = os.path.join(directory_name, "app.log")

logger = logging.getLogger("bus_booking_logger")
logger.setLevel(logging.INFO)

# ------Checking for Duplicate Loggers--------------------------
if not logger.handlers:
    
    file_handler = logging.FileHandler(file_path, mode="a", encoding="utf-8")
    file_formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    # -------Console Handler--------------
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(message)s"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

