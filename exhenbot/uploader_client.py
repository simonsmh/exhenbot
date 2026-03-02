import asyncio
import hashlib
import mimetypes
import os
from urllib.parse import urlparse

import aiobotocore.session
import httpx
from loguru import logger

from .utils import retry_request


class FileUploader:
    CATBOX_URL = "https://catbox.moe/user/api.php"
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

    async def _upload_s3(self, url: str) -> str:
        if not self.s3_config or not self.s3_config.get("endpoint"):
            return None

        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            content = resp.content
            content_type = resp.headers.get("content-type")
        except Exception as e:
            raise RuntimeError(f"Failed to download {url} for S3 upload: {e}")

        parsed = urlparse(url)
        path = parsed.path
        ext = os.path.splitext(path)[1]
        if not ext and content_type:
            ext = mimetypes.guess_extension(content_type) or ""
        if not ext:
            ext = ".jpg"

        filename = hashlib.md5(url.encode()).hexdigest() + ext

        # Add prefix/path if configured
        prefix = self.s3_config.get("prefix")
        if prefix:
            # Ensure prefix doesn't start with / but ends with /
            prefix = prefix.lstrip("/").rstrip("/") + "/"
            s3_key = prefix + filename
        else:
            s3_key = filename

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

    async def _upload_imgbb(self, url: str) -> str:
        if not self.imgbb_api_key:
            raise RuntimeError("IMGBB_API_KEY not configured")
        r = await retry_request(
            self.client,
            method="POST",
            url="https://api.imgbb.com/1/upload",
            data={"key": self.imgbb_api_key, "image": url},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success") and data.get("data", {}).get("url"):
            return data["data"]["url"]
        raise RuntimeError(f"imgbb upload failed: {data}")

    async def upload_url(self, url: str) -> str:
        async with self.semaphore:
            if self.imgbb_api_key:
                try:
                    uploaded_url = await self._upload_imgbb(url)
                    if await self._check_content(uploaded_url):
                        return uploaded_url
                    logger.warning(f"imgbb returned empty content for {url}")
                except Exception as e:
                    logger.warning(f"imgbb upload failed ({e}) for {url}")

            if self.s3_config and self.s3_config.get("endpoint"):
                try:
                    return await self._upload_s3(url)
                except Exception as e:
                    logger.warning(f"S3 upload failed ({e}) for {url}")

            try:
                uploaded_url = await self._upload_catbox(url)
                if await self._check_content(uploaded_url):
                    return uploaded_url
                logger.warning(
                    f"Catbox returned empty content, fallback to imgbb for {url}"
                )
            except Exception as e:
                logger.warning(
                    f"Catbox upload failed ({e}), fallback to imgbb for {url}"
                )

        raise RuntimeError(f"Upload failed for {url}")
