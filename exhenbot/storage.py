import base64
import json
from dataclasses import dataclass
from typing import List, Optional

from tortoise import Tortoise, fields, models

from .config import load_settings

settings = load_settings()


class Gallery(models.Model):
    gid = fields.IntField(pk=True)
    url = fields.CharField(max_length=64)
    tags = fields.JSONField()
    title = fields.TextField()
    telegraph_url = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    chat_ids = fields.JSONField(null=True)

    class Meta:
        table = f"{settings.table_prefix}gallery"


class Task(models.Model):
    chat_id = fields.BigIntField(pk=True)
    search = fields.TextField(default=settings.exh_query)
    catogories = fields.IntField(default=settings.exh_catogories)
    star = fields.IntField(default=settings.exh_star)
    author_name = fields.TextField(default=settings.telegraph_author_name)
    author_url = fields.TextField(default=settings.telegraph_author_url)
    query_depth = fields.IntField(default=settings.exh_query_depth)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = f"{settings.table_prefix}task"


@dataclass
class TaskData:
    search: str
    catogories: int
    star: int
    author_name: str
    author_url: str
    query_depth: int

    @staticmethod
    def from_text(text: str) -> "TaskData":
        try:
            decoded_bytes = base64.b64decode(text)
            decoded_str = decoded_bytes.decode("utf-8")
            data = json.loads(decoded_str)
            [k, v] = settings.task_check.split(":", 1)
            if data.get(k) != v:
                raise ValueError("Invalid task data")

            return TaskData(
                search=data.get("search"),
                catogories=data.get("catogories"),
                star=data.get("star"),
                author_name=data.get("author_name"),
                author_url=data.get("author_url"),
                query_depth=data.get("query_depth"),
            )
        except Exception:
            raise ValueError("Invalid task data")


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


async def get_task(chat_id: int) -> Optional[Task]:
    return await Task.filter(chat_id=chat_id).first()


async def get_all_tasks() -> List[Task]:
    return await Task.all().order_by("-created_at")


async def upsert_gallery(
    gid: int,
    url: str,
    tags: dict,
    title: str,
    telegraph_url: str,
    chat_id: int | None = None,
) -> Gallery:
    existing = await get_gallery(gid)
    if existing:
        existing.url = url
        existing.tags = tags
        existing.title = title
        existing.telegraph_url = telegraph_url
        existing.chat_ids = existing.chat_ids or []
        if chat_id and chat_id not in existing.chat_ids:
            existing.chat_ids.append(chat_id)
        await existing.save()
        return existing
    return await Gallery.create(
        gid=gid,
        url=url,
        tags=tags,
        title=title,
        telegraph_url=telegraph_url,
        chat_ids=[chat_id] if chat_id else [],
    )


async def upsert_task(
    chat_id: int,
    search: str,
    catogories: int,
    star: int,
    author_name: str,
    author_url: str,
    query_depth: int,
) -> Task:
    existing = await get_task(chat_id)
    if existing:
        existing.search = search
        existing.catogories = catogories
        existing.star = star
        existing.author_name = author_name
        existing.author_url = author_url
        existing.query_depth = query_depth
        await existing.save()
        return existing
    return await Task.create(
        chat_id=chat_id,
        search=search,
        catogories=catogories,
        star=star,
        author_name=author_name,
        author_url=author_url,
        query_depth=query_depth,
    )


async def delete_task(chat_id: int):
    await Task.filter(chat_id=chat_id).delete()
