from contextvars import ContextVar, Token
from typing import Callable

CrawlProgressEvent = dict[str, str | None]
CrawlProgressCallback = Callable[[CrawlProgressEvent], None]

_progress_callback: ContextVar[CrawlProgressCallback | None] = ContextVar(
    "crawl_progress_callback", default=None
)


def set_crawl_progress_callback(callback: CrawlProgressCallback) -> Token:
    return _progress_callback.set(callback)


def reset_crawl_progress_callback(token: Token) -> None:
    _progress_callback.reset(token)


def report_crawl_progress(
    message: str,
    *,
    platform: str | None = None,
    keyword: str | None = None,
    stage: str | None = None,
) -> None:
    callback = _progress_callback.get()
    if callback is not None:
        callback({
            "message": message,
            "platform": platform,
            "keyword": keyword,
            "stage": stage,
        })
