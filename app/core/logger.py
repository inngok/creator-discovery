import logging
import sys


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "[%(levelname)s] - [%(asctime)s] - [%(name)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File handler
        try:
            fh = logging.FileHandler("server.log", encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception:
            pass

    return logger


# Create a default logger for quick imports
logger = setup_logger("app")
