import logging
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


class ISTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=IST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]


_debug_val = str(os.getenv("DEBUG", "False")).strip().lower()
IS_DEBUG = _debug_val in ("true", "1", "t", "yes")

root_logger = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(ISTFormatter("%(asctime)s | %(levelname)s | %(name)s — %(message)s"))

if IS_DEBUG:
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("telethon").setLevel(logging.INFO)
    logging.getLogger("telethon.crypto").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
else:
    root_logger.setLevel(logging.CRITICAL)
    root_logger.addHandler(handler)
    for lib in ("httpx", "telegram", "telethon", "apscheduler", "asyncio", "httpcore"):
        logging.getLogger(lib).setLevel(logging.CRITICAL)

log = logging.getLogger("bot")
LOG = log

