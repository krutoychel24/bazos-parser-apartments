"""Source registry and fault-isolated multi-source fetching."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import olx_scraper
import scraper as bazos_scraper
from scraper import Ad, AdDetail

log = logging.getLogger(__name__)

SOURCE_LABELS = {"bazos": "Bazoš", "olx": "OLX"}


@dataclass
class FetchResult:
    by_source: dict[str, list[Ad]] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)

    @property
    def ads(self) -> list[Ad]:
        return [ad for ads in self.by_source.values() for ad in ads]


def enabled_sources(filters: dict) -> list[str]:
    raw = str(filters.get("sources", "bazos,olx"))
    selected = [source.strip() for source in raw.split(",")]
    return [source for source in ("bazos", "olx") if source in selected]


def fetch_all(filters: dict, timeout: int = 15) -> FetchResult:
    result = FetchResult()
    fetchers = {"bazos": bazos_scraper.fetch, "olx": olx_scraper.fetch}
    for source in enabled_sources(filters):
        try:
            result.by_source[source] = fetchers[source](filters, timeout=timeout)
        except Exception as exc:
            result.errors[source] = exc
            log.exception("%s fetch failed: %s", SOURCE_LABELS[source], exc)
    return result


def fetch_detail(ad: Ad, timeout: int = 15) -> AdDetail:
    if ad.source == "olx":
        return AdDetail(
            ad_id=ad.ad_id,
            images=list(ad.images),
            author=ad.author,
            description=ad.description,
        )
    return bazos_scraper.fetch_detail(ad.ad_id, ad.url, timeout=timeout)
