import logging


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the computeedge namespace."""
    return logging.getLogger(f"computeedge.{name}")
