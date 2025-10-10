import asyncio

import httpx
from loguru import logger

from .utils import retry_request


class FileUploader:
    CATBOX_URL = "https://catbox.moe/user/api.php"
    ZEROXZERO_URL = "https://0x0.st"
    FREEIMAGE_HOST_URL = "https://freeimage.host/api/1/upload"
    _HEADERS = {"user-agent": "PostmanRuntime/7.47.1"}

    def __init__(self, semaphore_size: int = 10, timeout: int = 30):
        self.client = httpx.AsyncClient(
            headers=self._HEADERS, timeout=timeout, follow_redirects=True, http2=True
        )
        self.semaphore = asyncio.Semaphore(semaphore_size)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _check_content_length(self, url: str) -> bool:
        try:
            resp = await self.client.head(url)
            length = int(resp.headers.get("Content-Length", "0"))
            return length > 0
        except Exception as e:
            logger.warning(f"HEAD request failed for {url}: {e}, trying GET request")
            return False

    async def _upload_catbox(self, url: str) -> str:
        data = {"reqtype": "urlupload", "userhash": "", "url": url}
        r = await retry_request(
            self.client,
            method="POST",
            url=self.CATBOX_URL,
            data=data,
        )
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"Catbox upload failed: {text}")

    async def _upload_0x0(self, url: str) -> str:
        data = {"url": url}
        r = await retry_request(
            self.client,
            method="POST",
            url=self.ZEROXZERO_URL,
            data=data,
        )
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"0x0.st upload failed: {text}")

    async def _upload_freeimagehost(self, url: str) -> str:
        data = {
            "key": "6d207e02198a847aa98d0a2a901485a5",
            "source": url,
            "format": "txt",
        }
        r = await retry_request(
            self.client, method="POST", url=self.FREEIMAGE_HOST_URL, data=data
        )
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"Freeimage.host upload failed: {text}")

    async def upload_url(self, url: str) -> str:
        async with self.semaphore:
            try:
                uploaded_url = await self._upload_catbox(url)
                if await self._check_content_length(uploaded_url):
                    return uploaded_url
                logger.warning(
                    f"Catbox returned empty content, fallback to freeimage.host for {url}"
                )
            except Exception as e:
                logger.warning(
                    f"Catbox upload failed ({e}), fallback to freeimage.host for {url}"
                )

            try:
                uploaded_url = await self._upload_freeimagehost(url)
                if await self._check_content_length(uploaded_url):
                    return uploaded_url
                logger.warning(
                    f"Freeimage.host returned empty content, fallback to 0x0.st for {url}"
                )
            except Exception as e:
                logger.warning(
                    f"Freeimage.host upload failed ({e}), fallback to 0x0.st for {url}"
                )

        #     try:
        #         uploaded_url = await self._upload_0x0(url)
        #         if await self._check_content_length(uploaded_url):
        #             return uploaded_url
        #         logger.warning(f"0x0.st returned empty content {url}")
        #     except Exception as e:
        #         logger.warning(f"0x0.st upload failed ({e}) {url}")

        raise RuntimeError(f"Upload failed for {url}")
