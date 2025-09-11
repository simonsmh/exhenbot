from typing import Optional

from tortoise import Tortoise, fields, models


class Gallery(models.Model):
    gid = fields.IntField(pk=True)
    url = fields.CharField(max_length=64)
    tags = fields.JSONField()
    title = fields.TextField()
    telegraph_url = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "gallery"


async def db_init(db_url: Optional[str]) -> None:
    await Tortoise.init(
        db_url=db_url or "sqlite://./cache.db",
        modules={"models": [__name__]},
    )
    await Tortoise.generate_schemas()


async def db_close() -> None:
    await Tortoise.close_connections()


async def get_gallery(gid: int) -> Optional[Gallery]:
    return await Gallery.filter(gid=gid).first()


async def upsert_gallery(
    gid: int, url: str, tags: dict, title: str, telegraph_url: str
) -> Gallery:
    existing = await get_gallery(gid)
    if existing:
        existing.url = url
        existing.tags = tags
        existing.title = title
        existing.telegraph_url = telegraph_url
        await existing.save()
        return existing
    return await Gallery.create(
        gid=gid, url=url, tags=tags, title=title, telegraph_url=telegraph_url
    )
