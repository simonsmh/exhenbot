import asyncio
from typing import List, Optional

import httpx
from loguru import logger

from .utils import retry_request


class CatboxUploader:
    API_URL = "https://catbox.moe/user/api.php"

    def __init__(self, userhash: Optional[str] = None, semaphore_size: int = 4):
        self.userhash = userhash
        self.client = httpx.AsyncClient(timeout=60)
        self.semaphore = asyncio.Semaphore(semaphore_size)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def upload_url(self, url: str) -> str:
        headers = {
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
        }
        data = {"reqtype": "urlupload", "userhash": "", "url": url}
        if self.userhash:
            data["userhash"] = self.userhash
        r = await retry_request(self.client, method="POST", url=self.API_URL, data=data, headers=headers)
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"Catbox upload failed: {text}")

    async def upload_image_urls(self, image_urls: List[str]) -> List[str]:
        
        async def upload_with_semaphore(url: str) -> Optional[str]:
            async with self.semaphore:
                try:
                    return await self.upload_url(url)
                except Exception as e:
                    logger.error(f"Catbox upload failed: {e}")
                    return None
        
        results = await asyncio.gather(*[upload_with_semaphore(url) for url in image_urls])
        return [url for url in results if url is not None]
