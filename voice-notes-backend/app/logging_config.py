# app/logging_config.py — one place that decides how every logger writes.
#
# Text for local dev, JSON lines for production (LOG_FORMAT=json) so a log
# shipper can index fields. Uvicorn's own loggers are routed through the same
# handler so the output is uniform.
import json, logging, logging.config
from datetime import datetime, timezone

# Keys a log record carries beyond the message, when passed via `extra=`.
_EXTRA_KEYS = ("user_id", "lang", "duration_s", "audio_bytes", "finals", "event", "status", "path", "method")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in _EXTRA_KEYS:
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={getattr(record, k)}" for k in _EXTRA_KEYS if hasattr(record, k))
        base = super().format(record)
        return f"{base} {extras}" if extras else base


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    formatter = "json" if fmt.lower() == "json" else "text"
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "text": {"()": TextFormatter, "format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                     "datefmt": "%H:%M:%S"},
            "json": {"()": JsonFormatter},
        },
        "handlers": {
            "default": {"class": "logging.StreamHandler", "formatter": formatter, "stream": "ext://sys.stdout"},
        },
        "root": {"handlers": ["default"], "level": level.upper()},
        "loggers": {
            # Uvicorn installs its own handlers; strip them so everything goes through ours once.
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": True},
            # Deepgram's SDK is chatty at DEBUG; keep it at WARNING unless we're debugging it.
            "deepgram": {"level": "WARNING"},
            "websockets": {"level": "WARNING"},
            "httpx": {"level": "WARNING"},
        },
    })
