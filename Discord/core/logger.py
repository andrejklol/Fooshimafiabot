import logging
import os
import sys
import queue
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

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


# ─────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────
class RateLimitFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        return "rate limited" not in msg


class _SubstringFilter(logging.Filter):
    """Drop log records whose rendered message contains any of the given substrings."""
    def __init__(self, *substrings: str):
        super().__init__()
        self._needles = [s.lower() for s in substrings]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        return not any(needle in msg for needle in self._needles)


# Suppress PyNaCl / davey "voice not supported" warnings from discord.client
_DISCORD_CLIENT_FILTER = _SubstringFilter("pynacl", "davey", "voice will not be supported")

# Suppress per-tick presence OK spam (logged every 60 s)
_PRESENCE_TICK_FILTER = _SubstringFilter("friend presence refresh tick ok")

# Suppress per-member status.initial lines on startup
_STATUS_INITIAL_FILTER = _SubstringFilter("status.initial")

# Suppress verbose module wiring / registry lines
_MODULE_WIRE_FILTER = _SubstringFilter("wired", "registered module=")


def _supports_color(stream) -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR") == "1":
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def _paint(text: str, color: str, use_color: bool) -> str:
    return f"{color}{text}{RESET}" if use_color else text


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
            line += "\n" + self.formatException(record.exc_info)

        return line


# ─────────────────────────────────────────────
# NON-BLOCKING LOGGING CORE
# ─────────────────────────────────────────────

_log_queue = queue.Queue(-1)
_listener = None


def setup_logging():
    global _listener

    stream = sys.stderr
    use_color = _supports_color(stream)

    # ── Queue handler (prevents blocking bot loop)
    queue_handler = QueueHandler(_log_queue)
    queue_handler.setLevel(logging.INFO)

    # ── Console handler
    console_handler = logging.StreamHandler(stream)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        ColoredFormatter(datefmt="%Y-%m-%d %H:%M:%S", use_color=use_color)
    )
    console_handler.addFilter(RateLimitFilter())
    console_handler.addFilter(_DISCORD_CLIENT_FILTER)
    console_handler.addFilter(_PRESENCE_TICK_FILTER)
    console_handler.addFilter(_STATUS_INITIAL_FILTER)
    console_handler.addFilter(_MODULE_WIRE_FILTER)

    handlers = [console_handler]

    # ── Optional file logging (crash recovery)
    log_file = os.getenv("LOG_FILE", "bot.log")
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        handlers.append(file_handler)
    except Exception:
        pass

    # ── Root logger setup
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(queue_handler)

    # ── Listener (async drain of logs)
    _listener = QueueListener(_log_queue, *handlers, respect_handler_level=True)
    _listener.start()

    # ── Noise suppression
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
        "Logging initialized. colors=%s file=%s",
        use_color,
        log_file,
    )


def shutdown_logging():
    global _listener
    if _listener:
        _listener.stop()
