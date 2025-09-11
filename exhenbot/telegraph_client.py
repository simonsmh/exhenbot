from typing import List, Optional
from telegraph.aio import Telegraph


class TelegraphClient:
    def __init__(self, access_token: str):
        self.telegraph = Telegraph(access_token=access_token)

    async def aclose(self) -> None:
        await self.telegraph._telegraph.session.aclose()

    async def create_telegraph_page(
        self,
        title: str,
        image_urls: List[str],
        author_name: str,
        author_url: Optional[str],
    ) -> str:
        if not self.telegraph.get_access_token():
            await self.telegraph.create_account(
                short_name=author_name, author_name=author_name, author_url=author_url
            )
        body = "\n".join(f'<p><img src="{u}"/></p>' for u in image_urls)
        graph_url = await self.telegraph.create_page(
            title=title,
            html_content=body,
            author_name=author_name,
            author_url=author_url,
        )
        if not graph_url:
            raise RuntimeError(f"Failed to create page: {graph_url}")
        return graph_url.get("url")
