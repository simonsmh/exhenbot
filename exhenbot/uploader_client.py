import asyncio
import base64
import hashlib
import mimetypes
import os
from typing import Optional, Tuple
from urllib.parse import urlparse

import aiobotocore.session
import httpx
from loguru import logger

from .utils import retry_request


class FileUploader:
    CATBOX_URL = "https://catbox.moe/user/api.php"
    IMGBB_URL = "https://api.imgbb.com/1/upload"
    _HEADERS = {"user-agent": "PostmanRuntime/7.47.1"}

    def __init__(
        self,
        semaphore_size: int = 10,
        timeout: int = 30,
        s3_config: dict = None,
        imgbb_api_key: str = None,
        proxy: str = None,
    ):
        self.client = httpx.AsyncClient(
            headers=self._HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=True,
            proxy=proxy or None,
        )
        self.semaphore = asyncio.Semaphore(semaphore_size)
        self.s3_config = s3_config
        self.imgbb_api_key = imgbb_api_key

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _check_content(self, url: str) -> bool:
        try:
            resp = await self.client.head(url)
            resp.raise_for_status()
            length = int(resp.headers.get("Content-Length", "0"))
            return length > 0
        except Exception as e:
            logger.warning(f"HEAD request failed for {url}: {e}")
            return False

    async def _download(self, url: str) -> Tuple[bytes, str, str]:
        """Download image and return (content, content_type, filename)."""
        resp = await self.client.get(url)
        resp.raise_for_status()
        content = resp.content
        content_type = resp.headers.get("content-type", "application/octet-stream")
        ext = mimetypes.guess_extension(content_type) or ".jpg"
        filename = hashlib.md5(url.encode()).hexdigest() + ext
        return content, content_type, filename

    # ------------------------------------------------------------------
    # URL-based uploads (remote service fetches the image itself)
    # ------------------------------------------------------------------

    async def _catbox_url_upload(self, url: str) -> str:
        r = await retry_request(
            self.client,
            method="POST",
            url=self.CATBOX_URL,
            data={"reqtype": "urlupload", "userhash": "", "url": url},
        )
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"Catbox URL upload failed: {text}")

    async def _imgbb_url_upload(self, url: str) -> str:
        if not self.imgbb_api_key:
            raise RuntimeError("IMGBB_API_KEY not configured")
        r = await retry_request(
            self.client,
            method="POST",
            url=self.IMGBB_URL,
            data={"key": self.imgbb_api_key, "image": url},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success") and data.get("data", {}).get("url"):
            return data["data"]["url"]
        raise RuntimeError(f"imgbb URL upload failed: {data}")

    # ------------------------------------------------------------------
    # File-based uploads (bot downloads first, then uploads)
    # ------------------------------------------------------------------

    async def _catbox_file_upload(self, content: bytes, content_type: str, filename: str) -> str:
        r = await retry_request(
            self.client,
            method="POST",
            url=self.CATBOX_URL,
            data={"reqtype": "fileupload", "userhash": ""},
            files={"fileToUpload": (filename, content, content_type)},
        )
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("http"):
            return text
        raise RuntimeError(f"Catbox file upload failed: {text}")

    async def _imgbb_file_upload(self, content: bytes, content_type: str, filename: str) -> str:
        if not self.imgbb_api_key:
            raise RuntimeError("IMGBB_API_KEY not configured")
        b64 = base64.b64encode(content).decode()
        r = await retry_request(
            self.client,
            method="POST",
            url=self.IMGBB_URL,
            data={"key": self.imgbb_api_key, "image": b64},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success") and data.get("data", {}).get("url"):
            return data["data"]["url"]
        raise RuntimeError(f"imgbb file upload failed: {data}")

    async def _s3_file_upload(self, content: bytes, content_type: str, filename: str) -> str:
        if not self.s3_config or not self.s3_config.get("endpoint"):
            raise RuntimeError("S3 not configured")
        prefix = self.s3_config.get("prefix", "")
        if prefix:
            prefix = prefix.lstrip("/").rstrip("/") + "/"
        s3_key = prefix + filename

        session = aiobotocore.session.get_session()
        async with session.create_client(
            "s3",
            endpoint_url=self.s3_config.get("endpoint"),
            aws_access_key_id=self.s3_config.get("access_key"),
            aws_secret_access_key=self.s3_config.get("secret_key"),
            region_name=self.s3_config.get("region"),
        ) as client:
            await client.put_object(
                Bucket=self.s3_config.get("bucket"),
                Key=s3_key,
                Body=content,
                ContentType=content_type or "application/octet-stream",
            )

        public_url_base = self.s3_config.get("public_url")
        if public_url_base:
            return f"{public_url_base.rstrip('/')}/{s3_key}"
        return f"{self.s3_config.get('endpoint').rstrip('/')}/{self.s3_config.get('bucket')}/{s3_key}"

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def upload_url(self, url: str) -> str:
        """Upload image from URL using a tiered fallback strategy:
        1. catbox URL upload  (remote fetch, no bot bandwidth)
        2. imgbb  URL upload  (remote fetch, no bot bandwidth)
        --- download image locally ---
        3. catbox file upload
        4. imgbb  file upload
        5. S3     file upload
        """
        async with self.semaphore:
            # Phase 1: URL-based uploads
            try:
                result = await self._catbox_url_upload(url)
                if await self._check_content(result):
                    return result
                logger.warning(f"Catbox URL upload returned empty content for {url}")
            except Exception as e:
                logger.warning(f"Catbox URL upload failed ({e}) for {url}")

            if self.imgbb_api_key:
                try:
                    result = await self._imgbb_url_upload(url)
                    if await self._check_content(result):
                        return result
                    logger.warning(f"imgbb URL upload returned empty content for {url}")
                except Exception as e:
                    logger.warning(f"imgbb URL upload failed ({e}) for {url}")

            # Phase 2: download once, then try file-based uploads
            try:
                content, content_type, filename = await self._download(url)
            except Exception as e:
                raise RuntimeError(f"Failed to download image for file upload: {e}")

            try:
                result = await self._catbox_file_upload(content, content_type, filename)
                if await self._check_content(result):
                    return result
                logger.warning(f"Catbox file upload returned empty content for {url}")
            except Exception as e:
                logger.warning(f"Catbox file upload failed ({e}) for {url}")

            if self.imgbb_api_key:
                try:
                    result = await self._imgbb_file_upload(content, content_type, filename)
                    if await self._check_content(result):
                        return result
                    logger.warning(f"imgbb file upload returned empty content for {url}")
                except Exception as e:
                    logger.warning(f"imgbb file upload failed ({e}) for {url}")

            if self.s3_config and self.s3_config.get("endpoint"):
                try:
                    return await self._s3_file_upload(content, content_type, filename)
                except Exception as e:
                    logger.warning(f"S3 file upload failed ({e}) for {url}")

        raise RuntimeError(f"All upload methods failed for {url}")
