import asyncio
from typing import List, Optional

import httpx
from loguru import logger

from .utils import retry_request


class FileUploader:
    CATBOX_URL = "https://catbox.moe/user/api.php"
    ZEROXZERO_URL = "https://0x0.st"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
        )
    }

    def __init__(self, userhash: Optional[str] = None, semaphore_size: int = 4):
        self.userhash = userhash
        self.client = httpx.AsyncClient(timeout=60)
        self.semaphore = asyncio.Semaphore(semaphore_size)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _check_content_length(self, url: str) -> bool:
        try:
            resp = await self.client.head(url, timeout=20)
            length = int(resp.headers.get("Content-Length", "0"))
            return length > 0
        except Exception as e:
            logger.warning(f"HEAD request failed for {url}: {e}, trying GET request")
            try:
                resp = await self.client.get(url, timeout=20)
                return len(resp.content) > 0
            except Exception as get_e:
                logger.warning(f"GET request also failed for {url}: {get_e}")
                return False

    async def _upload_catbox(self, url: str) -> str:
        data = {"reqtype": "urlupload", "userhash": self.userhash or "", "url": url}
        r = await retry_request(self.client, method="POST", url=self.CATBOX_URL, data=data, headers=self._HEADERS)
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"Catbox upload failed: {text}")

    async def _upload_0x0(self, url: str) -> str:
        data = {"url": url}
        r = await retry_request(self.client, method="POST", url=self.ZEROXZERO_URL, data=data, headers=self._HEADERS)
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"0x0.st upload failed: {text}")

    async def upload_url(self, url: str) -> str:
        try:
            uploaded_url = await self._upload_catbox(url)
            if await self._check_content_length(uploaded_url):
                return uploaded_url
            logger.warning(f"Catbox returned empty content, fallback to 0x0.st for {url}")
        except Exception as e:
            logger.warning(f"Catbox upload failed ({e}), fallback to 0x0.st for {url}")

        return await self._upload_0x0(url)

    async def upload_image_urls(self, image_urls: List[str]) -> List[str]:
        results: List[str] = []

        async def upload_with_semaphore(url: str) -> Optional[str]:
            async with self.semaphore:
                try:
                    return await self.upload_url(url)
                except Exception as e:
                    logger.error(f"Upload failed for {url}: {e}")
                    return None

        coros = [upload_with_semaphore(url) for url in image_urls]
        for coro in asyncio.as_completed(coros):
            result = await coro
            if result:
                results.append(result)

        return results
