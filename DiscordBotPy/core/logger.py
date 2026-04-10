import logging
import os
import sys

from colorama import init as colorama_init

colorama_init(strip=False, convert=True, autoreset=False)

RESET = "\x1b[0m"

COLORS = {
    "gray": "\x1b[90m",
    "white": "\x1b[97m",
    "yellow": "\x1b[93m",
    "red": "\x1b[91m",
    "green": "\x1b[92m",
    "cyan": "\x1b[96m",
    "pink": "\x1b[95m",
    "blue": "\x1b[94m",
}

LEVEL_COLORS = {
    "DEBUG": COLORS["gray"],
    "INFO": COLORS["white"],
    "WARNING": COLORS["yellow"],
    "ERROR": COLORS["red"],
    "CRITICAL": COLORS["pink"],
}

LOGGER_COLORS = {
    "main": COLORS["pink"],
    "vrchat_client": COLORS["cyan"],
    "status_pipeline": COLORS["green"],
    "core": COLORS["blue"],
    "services": COLORS["cyan"],
    "cogs": COLORS["green"],
}


def _supports_color(stream) -> bool:
    if os.getenv("NO_COLOR"):
        return False

    if os.getenv("FORCE_COLOR") == "1":
        return True

    is_a_tty = hasattr(stream, "isatty") and stream.isatty()
    term = os.getenv("TERM", "")
    session = os.getenv("TERM_SESSION_ID", "")

    return bool(is_a_tty or term or session)


def _paint(text: str, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{color}{text}{RESET}"


def color_message(msg: str, use_color: bool) -> str:
    if not use_color:
        return msg

    lower = msg.lower()

    if "error" in lower or "failed" in lower:
        return _paint(msg, COLORS["red"], use_color)

    if "connected" in lower or "success" in lower:
        return _paint(msg, COLORS["green"], use_color)

    if "cached" in lower or "loaded" in lower:
        return _paint(msg, COLORS["yellow"], use_color)

    if "pipeline" in lower:
        return _paint(msg, COLORS["cyan"], use_color)

    return msg


class ColoredFormatter(logging.Formatter):
    def __init__(self, *args, use_color: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level_text = f"{record.levelname:<8}"
        logger_text = f"{record.name:<15}"

        level_color = LEVEL_COLORS.get(record.levelname, COLORS["white"])
        logger_color = LOGGER_COLORS.get(record.name, COLORS["blue"])

        message = color_message(record.getMessage(), self.use_color)

        line = (
            f"{_paint(timestamp, COLORS['gray'], self.use_color)} | "
            f"{_paint(level_text, level_color, self.use_color)} | "
            f"{_paint(logger_text, logger_color, self.use_color)} | "
            f"{message}"
        )

        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            line = f"{line}\n{exc_text}"

        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"

        return line


def setup_logging():
    stream = sys.stderr
    use_color = _supports_color(stream)

    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        ColoredFormatter(
            datefmt="%Y-%m-%d %H:%M:%S",
            use_color=use_color,
        )
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)

    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.state").setLevel(logging.WARNING)
    logging.getLogger("discord.voice_client").setLevel(logging.ERROR)

    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.getLogger("core").setLevel(logging.INFO)
    logging.getLogger("services").setLevel(logging.INFO)
    logging.getLogger("cogs").setLevel(logging.INFO)

    logging.getLogger("main").info(
        "Logging initialized. colors=%s terminal=%s",
        use_color,
        getattr(stream, "isatty", lambda: False)(),
    )