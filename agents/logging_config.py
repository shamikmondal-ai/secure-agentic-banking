"""Shared logging setup for the transaction-triage pipeline.

Importing this module configures logging once, project-wide, at the level
set in config/settings.py. Every other module in agents/ just does:

    import logging_config  # noqa: F401  (side effect: configures logging)
    logger = logging.getLogger(__name__)

so log output is consistent everywhere without each module reinventing
logging.basicConfig(). This uses the stdlib `logging` module only -- no new
dependency -- with a small JSON formatter, for the same reason this project
uses structured decision fields elsewhere instead of free text: a plain
log line is hard to grep or alert on reliably; a JSON one isn't.
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import LOG_LEVEL  # noqa: E402

_configured = False


class JsonFormatter(logging.Formatter):
    """Renders each log record as one JSON object per line.

    Any call site that wants structured fields beyond the plain message
    passes them as extra={"context": {...}}; that dict is merged directly
    into the output. Nothing else is auto-discovered from the LogRecord --
    explicit is simpler to reason about than reflecting over internals.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload.update(context)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = LOG_LEVEL) -> None:
    """Configure the root logger once. Safe to call again -- later calls
    just update the level rather than adding a duplicate handler."""
    global _configured
    root = logging.getLogger()
    if not _configured:
        # stderr, not stdout: operational logs are diagnostic output, not a
        # CLI tool's actual product. Piping stdout (e.g.
        # `python evals/run_evals.py > results.txt`) should capture a clean
        # report; logs still reach the terminal via stderr, and can be
        # filtered or redirected independently.
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        _configured = True
    root.setLevel(level)


configure_logging()
