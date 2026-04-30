"""bazos.sk reality scraper — list + detail page parsing."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://reality.bazos.sk"
LIST_PATH = "/prenajmu/byt/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(r"(\d[\d\s]*)")
IMG_RE = re.compile(r"https?://(?:www\.)?bazos\.sk/img/(\d+)/(\d+)/(\d+)\.jpg")
THUMB_RE = re.compile(r"https?://(?:www\.)?bazos\.sk/img/(\d+)t/(\d+)/(\d+)\.jpg")


@dataclass
class Ad:
    ad_id: str
    title: str
    url: str
    price: int | None
    location: str
    image: str | None
    description: str

    def telegram_text(self) -> str:
        price = f"{self.price} €" if self.price is not None else "—"
        desc = self.description[:300] + ("…" if len(self.description) > 300 else "")
        return (
            f"<b>{_escape(self.title)}</b>\n"
            f"💰 {price}  |  📍 {_escape(self.location)}\n"
            f"{_escape(desc)}\n"
            f"{self.url}"
        )


@dataclass
class AdDetail:
    ad_id: str
    images: list[str] = field(default_factory=list)
    author: str = ""
    description: str = ""
    views: int | None = None


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_url(filters: dict) -> str:
    """Build search URL from filter dict."""
    params = {
        "hledat": filters.get("hledat", ""),
        "rubriky": "reality",
        "hlokalita": filters.get("hlokalita", ""),
        "humkreis": filters.get("humkreis", ""),
        "cenaod": filters.get("cenaod", ""),
        "cenado": filters.get("cenado", ""),
        "Submit": "Hľadať",
        "order": filters.get("order", ""),
        "crp": "",
        "kitx": "ano",
    }
    return f"{BASE}{LIST_PATH}?{urlencode(params)}"


def _parse_price(raw: str) -> int | None:
    if not raw:
        return None
    m = PRICE_RE.search(raw.replace("\xa0", " "))
    if not m:
        return None
    return int(m.group(1).replace(" ", ""))


def _ad_id_from_href(href: str) -> str | None:
    m = re.search(r"/inzerat/(\d+)/", href)
    return m.group(1) if m else None


def parse_ads(html: str) -> list[Ad]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Ad] = []
    for block in soup.select(".inzeraty"):
        title_a = block.select_one("h2.nadpis a")
        if not title_a:
            continue
        href = title_a.get("href", "")
        ad_id = _ad_id_from_href(href)
        if not ad_id:
            continue
        url = href if href.startswith("http") else f"{BASE}{href}"
        title = title_a.get_text(strip=True)

        loc_el = block.select_one(".inzeratylok")
        location = loc_el.get_text(" ", strip=True) if loc_el else ""

        price_el = block.select_one(".inzeratycena")
        price = _parse_price(price_el.get_text(" ", strip=True)) if price_el else None

        img_el = block.select_one("img.obrazek")
        image = img_el.get("src") if img_el else None

        desc_el = block.select_one(".popis")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""

        out.append(Ad(ad_id, title, url, price, location, image, desc))
    return out


def fetch(filters: dict, timeout: int = 15) -> list[Ad]:
    url = build_url(filters)
    log.info("fetching %s", url)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return parse_ads(r.text)


def parse_detail(html: str, ad_id: str) -> AdDetail:
    """Parse detail page: extract all image URLs (full-size), author, description."""
    soup = BeautifulSoup(html, "html.parser")
    detail = AdDetail(ad_id=ad_id)

    # Images: only those matching THIS ad id (page also shows related ads).
    # Full-size pattern: /img/<N>/<sub>/<ad_id>.jpg ; ignore thumbs (<N>t/...).
    full_seen: dict[int, str] = {}
    for m in IMG_RE.finditer(html):
        n, _sub, found_id = m.group(1), m.group(2), m.group(3)
        if found_id != ad_id:
            continue
        full_seen[int(n)] = m.group(0)
    # If no full-size found, fall back to thumbs for this ad.
    if not full_seen:
        for m in THUMB_RE.finditer(html):
            n, _sub, found_id = m.group(1), m.group(2), m.group(3)
            if found_id != ad_id:
                continue
            full_seen[int(n)] = m.group(0)
    detail.images = [full_seen[k] for k in sorted(full_seen)]

    # Author name — first paction span next to "Meno:"
    for tr in soup.select("tr"):
        txt = tr.get_text(" ", strip=True)
        if txt.startswith("Meno"):
            span = tr.select_one("span.paction") or tr.select_one("td b")
            if span:
                detail.author = span.get_text(strip=True)
            break

    # Views
    for tr in soup.select("tr"):
        txt = tr.get_text(" ", strip=True)
        if "Videlo" in txt:
            mv = re.search(r"(\d[\d\s]*)", txt)
            if mv:
                detail.views = int(mv.group(1).replace(" ", "").replace("\xa0", ""))
            break

    # Full description block
    desc_el = soup.select_one(".popisdetail")
    if desc_el:
        detail.description = desc_el.get_text(" ", strip=True)
    else:
        # fallback: try generic content area
        for d in soup.select("div"):
            cls = " ".join(d.get("class", []))
            if "popis" in cls.lower() and len(d.get_text(strip=True)) > 50:
                detail.description = d.get_text(" ", strip=True)
                break

    return detail


def fetch_detail(ad_id: str, ad_url: str, timeout: int = 15) -> AdDetail:
    log.info("fetching detail %s", ad_url)
    r = requests.get(ad_url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return parse_detail(r.text, ad_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ads = fetch({"hlokalita": "04001", "humkreis": "10", "cenado": "550"})
    print(f"got {len(ads)} ads")
    for a in ads[:3]:
        print(a.ad_id, a.price, a.title, "|", a.location)
    if ads:
        d = fetch_detail(ads[0].ad_id, ads[0].url)
        print(f"detail: author={d.author!r} views={d.views} imgs={len(d.images)}")
        for img in d.images[:5]:
            print(" ", img)
