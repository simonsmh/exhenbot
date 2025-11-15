import asyncio
import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger
from lxml import html as lxml_html

from .utils import retry_request


@dataclass
class GalleryEntry:
    gid: int
    url: str
    title: str
    tags: List[str]


@dataclass
class GalleryInfo:
    gid: int
    url: str
    title: str
    tags: List[str]


@dataclass
class MpvImageEntry:
    """Create entry from imagelist item.

    Expects keys:
    - n: filename
    - k: imgkey
    - t: optional thumbnail url
    """

    index: int
    filename: str
    imgkey: str
    thumbnail: Optional[str] = None

    @classmethod
    def from_dict(cls, idx: int, data: Dict[str, str]) -> "MpvImageEntry":
        if "n" not in data or "k" not in data:
            raise ValueError(f"Invalid imagelist item: {data!r}")
        return cls(
            index=idx + 1,
            filename=data.get("n"),
            imgkey=data.get("k"),
            thumbnail=data.get("t"),
        )


@dataclass
class MpvInfo:
    gid: int
    token: str
    mpv_url: str
    pagecount: int
    mpvkey: Optional[str]
    images: List[MpvImageEntry]


@dataclass
class ImageDispatch:
    """
    Parsed response from ExHentai `imagedispatch` API.

    Expects keys:
    - d: Display size and file size label, e.g. "1280 x 1839 :: 214.2 KiB".
    - o: Optional label for the original download link, e.g. "Download original 1734 x 2491 1.41 MiB source".
    - lf: Relative path to the full-size JPEG, e.g. "fullimg/<gid>/<page>/<hash>/img0.jpg".
    - ls: Query string used for the webp source (fs_from, shash, etc.).
    - ll: Relative path to the webp image, e.g. "<hash>-<id>-<w>-<h>-jpg/.../img0.webp".
    - lo: Relative directory for the original source, e.g. "s/<prefix>/<gid>-<page>".
    - xres: Image width in pixels.
    - yres: Image height in pixels.
    - i: Absolute URL to the image to fetch (usually webp when using MPV).
    - s: Server slot/token identifier used by the image CDN.
    """

    d: str
    o: str
    lf: str
    ls: str
    ll: str
    lo: str
    xres: str
    yres: str
    i: str
    s: str

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ImageDispatch":
        if not isinstance(data, dict) or "i" not in data:
            raise RuntimeError(f"Unexpected imagedispatch response: {data!r}")
        return cls(
            d=data.get("d"),
            o=data.get("o"),
            lf=data.get("lf"),
            ls=data.get("ls"),
            ll=data.get("ll"),
            lo=data.get("lo"),
            xres=data.get("xres"),
            yres=data.get("yres"),
            i=data["i"],
            s=data.get("s"),
        )


class ExHentaiClient:
    BASE_URL = "https://exhentai.org"
    API_URL = "https://s.exhentai.org/api.php"
    RESET_URL = "https://e-hentai.org/home.php"

    def __init__(self, cookie_header: Optional[str] = None, semaphore_size: int = 4):
        headers = {}
        if cookie_header:
            headers["Cookie"] = cookie_header
        self.client = httpx.AsyncClient(
            headers=headers, follow_redirects=True, http2=True
        )
        self.semaphore = asyncio.Semaphore(semaphore_size)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def reset_gp(self):
        try:
            data = {"reset_imagelimit": "Reset Quota"}
            resp = await retry_request(
                self.client, method="POST", url=self.RESET_URL, data=data
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to reset GP quota: {e}")

    # -----------------------------
    # Search
    # -----------------------------
    async def search_galleries(
        self,
        search: str,
        catogories: int = 761,
        star: int = 4,
        next_gid: Optional[int] = None,
    ) -> Tuple[List[GalleryEntry], int | None]:
        """Search gallery list by query and return gallery entries.

        Parses each gallery row to extract:
        - href (absolute URL)
        - .glink text as title
        - all .gt @title values as tags

        Pagination:
        - If `next_gid` is provided, uses `next=<gid>` which is how ExHentai paginates.
        - After parsing, `self.last_search_next_gid` is set to the last gid from the page.
        """
        params = {
            "advsearch": 1,
            "f_cats": catogories,
            "f_search": search,
            "f_srdd": star,
            "f_sfl": "on",
            "f_sfu": "on",
            "f_sft": "on",
        }
        if next_gid is not None:
            params["next"] = next_gid

        resp = await retry_request(
            self.client, method="GET", url=self.BASE_URL + "/", params=params
        )
        resp.raise_for_status()
        doc = lxml_html.fromstring(resp.text)

        # Prefer structured rows under the name cell which contains href, glink, and gt tags
        entries: List[GalleryEntry] = []
        last_gid_value: Optional[int] = None
        anchors = doc.xpath('//td[contains(@class,"glname")]//a[contains(@href,"/g/")]')
        if not anchors:
            anchors = doc.xpath(
                '//td[contains(@class,"gl2e")]//a[contains(@href,"/g/")]'
            )
        if not anchors:
            anchors = doc.xpath(
                '//div[contains(@class,"gl1t")]//a[contains(@href,"/g/")]'
            )
        for a in anchors:
            href = a.get("href")
            if not href:
                continue
            url = urllib.parse.urljoin(self.BASE_URL, href)

            # Extract gid from URL path without regex; update last gid seen
            path = urllib.parse.urlparse(url).path.strip("/")
            segments = path.split("/")
            try:
                idx = segments.index("g")
                gid_candidate = int(segments[idx + 1])
                last_gid_value = gid_candidate
            except Exception:
                pass

            glink_nodes = a.xpath('.//div[@class="glink"]')
            title = (
                glink_nodes[0].text_content().strip()
                if glink_nodes
                else a.text_content().strip()
            )
            tags = [t for t in a.xpath('.//div[@class="gt"]/@title') if t]
            entries.append(
                GalleryEntry(gid=gid_candidate, url=url, title=title, tags=tags)
            )

        return entries, last_gid_value

    async def get_gallery_info(self, gallery_url: str) -> GalleryInfo:
        resp = await retry_request(self.client, method="GET", url=gallery_url)
        resp.raise_for_status()
        doc = lxml_html.fromstring(resp.text)

        # Parse gid from URL (/g/<gid>/<token>/...)
        gid = None
        path = urllib.parse.urlparse(gallery_url).path.strip("/")
        segments = path.split("/")
        try:
            idx = segments.index("g")
            gid = int(segments[idx + 1])
        except Exception:
            gid = None

        # Title: prefer english title in #gj, fallback to #gn or <title>
        title = None
        title_node = doc.xpath('//h1[@id="gj"]')
        if title_node:
            title = title_node[0].text_content().strip()
        if not title:
            title_node = doc.xpath('//h1[@id="gn"]')
            if title_node:
                title = title_node[0].text_content().strip()
        if not title:
            title = (doc.xpath("//title/text()") or [""])[0].strip()

        # Tags: prefer explicit tag ids (ta_namespace:tag) -> "namespace:tag"
        tags: List[str] = []
        for a in doc.xpath("//div[@id='taglist']//a[starts-with(@id,'ta_')]"):
            aid = a.get("id")
            if aid and aid.startswith("ta_"):
                tags.append(aid[len("ta_") :].replace("+", " "))
                continue
            href = a.get("href", "")
            if href:
                p = urllib.parse.unquote(urllib.parse.urlparse(href).path)
                parts = p.strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "tag":
                    tags.append(parts[1].replace("+", " "))
                    continue
            text = a.text_content().strip()
            if text:
                tags.append(text)

        return GalleryInfo(gid=gid, url=gallery_url, title=title, tags=tags)

    # -----------------------------
    # MPV parsing
    # -----------------------------
    async def fetch_mpv_info(self, gallery_url: str) -> MpvInfo:
        mpv_url = gallery_url.replace("/g/", "/mpv/")
        resp = await retry_request(self.client, method="GET", url=mpv_url)
        resp.raise_for_status()
        text = resp.text

        # Extract gid and token from the URL
        url_parts = mpv_url.split("/")
        try:
            mpv_index = url_parts.index("mpv")
        except (ValueError, IndexError):
            raise ValueError("Cannot parse gid/token from mpv URL: " + mpv_url)
        gid = int(url_parts[mpv_index + 1])
        token = url_parts[mpv_index + 2]

        # Extract pagecount
        pagecount = 0
        m = re.search(r"var\s+pagecount\s*=\s*(\d+)", text)
        if m:
            pagecount = int(m.group(1))

        # Extract mpvkey from inline script if present
        mpvkey = None
        pattern = r"var\s+mpvkey\s*=\s*\"([0-9a-zA-Z]+)\""
        mm = re.search(pattern, text)
        if mm:
            mpvkey = mm.group(1)

        # Parse imagelist strictly via JSON and extract t webp URL
        images: List[MpvImageEntry] = []
        imagelist_json_match = re.search(r"var\s+imagelist\s*=\s*(\[[\s\S]*?\]);", text)
        if not imagelist_json_match:
            raise ValueError("imagelist not found in MPV page")
        raw = imagelist_json_match.group(1)
        data = json.loads(raw)
        for idx, item in enumerate(data):
            images.append(MpvImageEntry.from_dict(idx, item))

        return MpvInfo(
            gid=gid,
            token=token,
            mpv_url=mpv_url,
            pagecount=pagecount,
            mpvkey=mpvkey,
            images=images,
        )

    # -----------------------------
    # API calls
    # -----------------------------
    async def imagedispatch(
        self, gid: int, page: int, imgkey: str, mpvkey: str, s: Optional[str] = None
    ) -> ImageDispatch:
        async with self.semaphore:
            payload = {
                "method": "imagedispatch",
                "gid": gid,
                "page": page,
                "imgkey": imgkey,
                "mpvkey": mpvkey,
            }
            if s is not None:
                payload["s"] = s
            r = await retry_request(
                self.client, method="POST", url=self.API_URL, json=payload
            )
            r.raise_for_status()
            data = r.json()
            ## check if result's i URL is accessable or retry with s
            if data and data.get("i") is not None:
                resp = await self.client.head(data["i"])
                if resp.status_code != 200 and data.get("s") is not None:
                    logger.warning(
                        f"Image dispatch failed, retrying with s: {data['i']}"
                    )
                    return await self.imagedispatch(
                        gid, page, imgkey, mpvkey, data["s"]
                    )
            return ImageDispatch.from_dict(data)


class EhTagConverter:
    """Converter for Ehentai tags using EhTagTranslation database."""

    DB_URL = (
        "https://cdn.jsdelivr.net/gh/EhTagTranslation/Database@release/db.full.json"
    )
    SHA_URL = "https://cdn.jsdelivr.net/gh/EhTagTranslation/Database@release/sha"

    def __init__(self, local_dir: str):
        self.client = httpx.AsyncClient(follow_redirects=True, http2=True)
        self.data: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.sha: Optional[str] = None
        self._loaded = False
        self.cache_dir = Path(local_dir)
        self.db_cache_file = self.cache_dir / "db.full.json"
        self.sha_cache_file = self.cache_dir / "sha"

    async def _fetch_remote_sha(self) -> str:
        """Fetch the remote SHA hash."""
        resp = await retry_request(self.client, method="GET", url=self.SHA_URL)
        resp.raise_for_status()
        return resp.text.strip()

    async def _fetch_remote_db(self) -> Dict:
        """Fetch the remote database."""
        resp = await retry_request(self.client, method="GET", url=self.DB_URL)
        resp.raise_for_status()
        return resp.json()

    def _load_cached_sha(self) -> Optional[str]:
        """Load SHA from cache file."""
        if self.sha_cache_file.exists():
            try:
                return self.sha_cache_file.read_text().strip()
            except Exception:
                return None
        return None

    def _load_cached_db(self) -> Optional[Dict]:
        """Load database from cache file."""
        if self.db_cache_file.exists():
            try:
                with open(self.db_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _save_sha_cache(self, sha: str) -> None:
        """Save SHA to cache file."""
        self.cache_dir.mkdir(exist_ok=True)
        self.sha_cache_file.write_text(sha)

    def _save_db_cache(self, data: Dict) -> None:
        """Save database to cache file."""
        self.cache_dir.mkdir(exist_ok=True)
        with open(self.db_cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def _ensure_cache_dir(self) -> None:
        """Ensure cache directory exists."""
        self.cache_dir.mkdir(exist_ok=True)

    async def load_database(self, force_update: bool = False) -> None:
        """Load the EhTagTranslation database, checking SHA version before use.

        Args:
            force_update: If True, force download even if cache is valid
        """
        if self._loaded and not force_update:
            return

        # Ensure cache directory exists
        await self._ensure_cache_dir()

        # Get remote SHA
        try:
            remote_sha = await self._fetch_remote_sha()
        except Exception as e:
            logger.warning(f"Failed to fetch remote SHA: {e}")
            remote_sha = None

        # Check if we have cached data
        cached_sha = self._load_cached_sha()
        cached_data = self._load_cached_db()

        # Determine if we need to update
        needs_update = force_update or remote_sha is None or cached_sha != remote_sha

        if needs_update:
            if remote_sha is None:
                logger.warning("Using cached data due to network issues")
                if cached_data is None:
                    raise RuntimeError(
                        "No cached data available and network is unreachable"
                    )
                self.data = cached_data
                self.sha = cached_sha
            else:
                logger.info("Downloading latest database...")
                try:
                    self.data = await self._fetch_remote_db()
                    self.sha = remote_sha
                    # Save to cache
                    self._save_sha_cache(remote_sha)
                    self._save_db_cache(self.data)
                    logger.info(
                        f"Database updated successfully (SHA: {remote_sha[:8]})"
                    )
                except Exception as e:
                    logger.warning(f"Failed to download database: {e}")
                    if cached_data is None:
                        raise RuntimeError(
                            "Failed to download database and no cache available"
                        )
                    logger.info("Using cached data")
                    self.data = cached_data
                    self.sha = cached_sha
        else:
            logger.info(f"Using cached database (SHA: {cached_sha[:8]})")
            self.data = cached_data
            self.sha = cached_sha

        self._loaded = True

    def translate_tag(self, tag_str: str) -> tuple[str, str]:
        namespace, tag = tag_str.replace("_", " ").split(":")
        namespace_index = next(
            (
                index
                for index, item in enumerate(self.data["data"])
                if item.get("namespace") == namespace
            ),
            None,
        )
        if namespace_index is None:
            return namespace.replace(" ", "_"), tag.replace(" ", "_")
        namespace_name = self.data["data"][namespace_index]["frontMatters"]["name"]
        tag_data = self.data["data"][namespace_index]["data"].get(tag)
        if tag_data is None:
            return namespace_name.replace(" ", "_"), tag.replace(" ", "_")
        tag_name = tag_data.get("name", {}).get("text")
        if tag_name is None:
            return namespace_name.replace(" ", "_"), tag.replace(" ", "_")
        return namespace_name.replace(" ", "_"), tag_name.replace(" ", "_")

    def batch_translate_tags(self, tag_strs: List[str]) -> dict[str, List[str]]:
        results = {}
        for tag_str in tag_strs:
            namespace, tag = self.translate_tag(tag_str)
            if namespace not in results:
                results[namespace] = []
            results[namespace].append(tag)
        return results

    async def aclose(self) -> None:
        """Close the HTTP client if we created it."""
        if hasattr(self, "client") and self.client:
            await self.client.aclose()
