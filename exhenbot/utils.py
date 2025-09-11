import asyncio

import httpx
from loguru import logger


async def retry_request(
    client: httpx.AsyncClient,
    *args,
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    **kwargs,
) -> httpx.Response:
    """Retry POST request with exponential backoff."""
    for attempt in range(max_retries + 1):
        try:
            response = await client.request(*args, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            if attempt == max_retries:
                logger.error(f"Request failed after {max_retries + 1} attempts: {e}")
                raise
            wait_time = backoff_factor * (2**attempt)
            logger.warning(
                f"Request failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {wait_time:.1f}s: {e}"
            )
            await asyncio.sleep(wait_time)
