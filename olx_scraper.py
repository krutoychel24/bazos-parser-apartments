"""OLX.ua apartment-rental scraper backed by OLX's JSON search endpoint."""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from scraper import Ad

log = logging.getLogger(__name__)

BASE = "https://www.olx.ua"
API_URL = os.environ.get("OLX_API_URL", f"{BASE}/api/v1/offers/")
CATEGORY_ID = os.environ.get("OLX_CATEGORY_ID", "1760")
LIMIT = 40
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LIST_PATH = "/uk/nedvizhimost/kvartiry/dolgosrochnaya-arenda-kvartir"
LOCATION_RE = re.compile(r"region_id=(\d+)&(?:amp;)?city_id=(\d+)")
KNOWN_LOCATIONS = {
    "киев": ("25", "268"),
    "київ": ("25", "268"),
    "kiev": ("25", "268"),
    "kyiv": ("25", "268"),
    "кременчуг": ("15", "221"),
    "кременчук": ("15", "221"),
    "kremenchug": ("15", "221"),
    "kremenchuk": ("15", "221"),
}

TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g",
        "д": "d", "е": "e", "ё": "e", "є": "ye", "ж": "zh",
        "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
        "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
        "ю": "yu", "я": "ya", "’": "", "'": "",
    }
)


def _location_slug(value: str) -> str:
    transliterated = value.casefold().translate(TRANSLIT)
    return re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")


@lru_cache(maxsize=128)
def _resolve_location_name(normalized_name: str) -> tuple[str, str]:
    known = KNOWN_LOCATIONS.get(normalized_name)
    if known:
        return known

    slug = _location_slug(normalized_name)
    if not slug:
        raise ValueError("empty OLX city name")
    url = f"{BASE}{LIST_PATH}/{slug}/"
    log.info("resolving OLX location %r via %s", normalized_name, url)
    response = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    response.raise_for_status()
    match = LOCATION_RE.search(response.text)
    if not match:
        raise ValueError(f"OLX city not found: {normalized_name}")
    return match.group(1), match.group(2)


def resolve_location(value: str) -> dict[str, str]:
    """Resolve a city name or ``region_id[:city_id]`` to OLX API params."""
    value = (value or "").strip()
    if not value:
        return {}
    if re.fullmatch(r"\d+(?::\d+)?", value):
        region_id, separator, city_id = value.partition(":")
        params = {"region_id": region_id}
        if separator:
            params["city_id"] = city_id
        return params

    region_id, city_id = _resolve_location_name(value.casefold())
    return {"region_id": region_id, "city_id": city_id}


def build_params(filters: dict) -> dict[str, str | int]:
    order = {
        "": "created_at:desc",
        "1": "filter_float_price:asc",
        "2": "filter_float_price:desc",
    }.get(str(filters.get("order", "")), "created_at:desc")
    params: dict[str, str | int] = {
        "offset": 0,
        "limit": LIMIT,
        "category_id": CATEGORY_ID,
        "sort_by": order,
    }
    params.update(resolve_location(str(filters.get("olx_location", ""))))
    if filters.get("hledat"):
        params["query"] = str(filters["hledat"])
    if filters.get("olx_cenaod"):
        params["filter_float_price:from"] = str(filters["olx_cenaod"])
    if filters.get("olx_cenado"):
        params["filter_float_price:to"] = str(filters["olx_cenado"])
    return params


def build_url(filters: dict) -> str:
    return f"{API_URL}?{urlencode(build_params(filters))}"


def _plain_text(value: object) -> str:
    if not value:
        return ""
    text = str(value)
    if "<" not in text and "&" not in text:
        return text.strip()
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _price(item: dict) -> tuple[int | None, str]:
    for param in item.get("params") or []:
        if param.get("key") != "price":
            continue
        value = param.get("value") or {}
        raw = value.get("value")
        try:
            price = int(float(raw)) if raw is not None else None
        except (TypeError, ValueError):
            price = None
        return price, str(value.get("currency") or "UAH")
    return None, "UAH"


def _photo_url(photo: dict) -> str | None:
    link = photo.get("link")
    if not link:
        return None
    # The API returns a size template. 1280 px is accepted by Telegram while
    # retaining enough detail for apartment photos.
    return str(link).replace("{width}", "1280").replace("{height}", "1280")


def parse_payload(payload: dict) -> list[Ad]:
    out: list[Ad] = []
    seen: set[str] = set()
    for item in payload.get("data") or []:
        raw_id = item.get("id")
        url = item.get("url")
        title = _plain_text(item.get("title"))
        if raw_id is None or not url or not title:
            continue

        # Prefixing prevents an OLX numeric ID from colliding with a Bazos ID
        # in the existing (chat_id, ad_id) SQLite primary key.
        ad_id = f"olx:{raw_id}"
        if ad_id in seen:
            continue
        seen.add(ad_id)

        price, currency = _price(item)
        location_data = item.get("location") or {}
        city = (location_data.get("city") or {}).get("name") or ""
        region = (location_data.get("region") or {}).get("name") or ""
        location = ", ".join(part for part in (city, region) if part)

        images = [
            image
            for image in (_photo_url(photo) for photo in item.get("photos") or [])
            if image
        ]
        user = item.get("user") or {}
        author = _plain_text(user.get("name"))
        out.append(
            Ad(
                ad_id=ad_id,
                title=title,
                url=str(url),
                price=price,
                location=location,
                image=images[0] if images else None,
                description=_plain_text(item.get("description")),
                source="olx",
                currency=currency,
                images=images,
                author=author,
            )
        )
    return out


def fetch(filters: dict, timeout: int = 15) -> list[Ad]:
    url = build_url(filters)
    log.info("fetching %s", url)
    response = requests.get(
        API_URL,
        params=build_params(filters),
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("OLX response does not contain a data list")
    return parse_payload(payload)
