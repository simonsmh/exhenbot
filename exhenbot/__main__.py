import re
from typing import Any

from loguru import logger
from telegram import (
    Message,
    MessageEntity,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

from .catbox_client import CatboxUploader
from .config import load_settings
from .exhentai_client import EhTagConverter, ExHentaiClient
from .storage import (
    Gallery,
    TaskData,
    db_close,
    db_init,
    delete_task,
    get_all_tasks,
    get_gallery,
    upsert_gallery,
    upsert_task,
)
from .telegraph_client import TelegraphClient

EHENTAI_URL_REGEX = r"https://e.hentai\.org/g/\d+/\w+"

settings = load_settings()
client = ExHentaiClient(
    cookie_header=settings.exh_cookie, semaphore_size=settings.exh_semaphore_size
)
catbox = CatboxUploader(
    userhash=settings.catbox_userhash, semaphore_size=settings.catbox_semaphore_size
)
telegraph = TelegraphClient(access_token=settings.telegraph_token)
ehtag = EhTagConverter(local_dir=settings.local_dir)


async def parse_url(
    url: str,
    send_if_exists: bool = True,
    author_name: str | None = None,
    author_url: str | None = None,
) -> Gallery | None:
    if author_name is None:
        author_name = settings.telegraph_author_name
    if author_url is None:
        author_url = settings.telegraph_author_url
    gallery_info = await client.get_gallery_info(url)
    logger.info(f"Parsing gallery: {gallery_info.gid} {gallery_info.title}")
    exist = await get_gallery(gallery_info.gid)
    if exist is not None:
        logger.info(f"Gallery already exists: {gallery_info.gid} {gallery_info.title}")
        if not send_if_exists:
            return
        return exist
    mpv_info = await client.fetch_mpv_info(url)
    logger.info(f"Fetching MPV info: {gallery_info.gid} {gallery_info.title}")
    img_urls = await client.resolve_image_urls(mpv_info)
    logger.info(f"Uploading image URLs: {gallery_info.gid} {gallery_info.title}")
    catbox_urls = await catbox.upload_image_urls(img_urls)
    logger.info(f"Translating tags: {gallery_info.gid} {gallery_info.title}")
    await ehtag.load_database()
    tags_dict = ehtag.batch_translate_tags(gallery_info.tags)
    for namespace, tags in tags_dict.items():
        logger.info(f"{namespace}: {tags}")
    logger.info(f"Creating telegraph page: {gallery_info.gid} {gallery_info.title}")
    telegraph_url = await telegraph.create_telegraph_page(
        title=gallery_info.title,
        image_urls=catbox_urls,
        author_name=author_name,
        author_url=author_url,
    )
    logger.info(f"Upserting gallery: {gallery_info.gid} {gallery_info.title}")
    return await upsert_gallery(
        gid=gallery_info.gid,
        url=gallery_info.url,
        tags=tags_dict,
        title=gallery_info.title,
        telegraph_url=telegraph_url,
    )


async def job_process(context: ContextTypes.DEFAULT_TYPE):
    tasks = await get_all_tasks()
    logger.info(f"Found {len(tasks)} tasks")
    for t in tasks:
        logger.info(
            f"Parsing task: {t.chat_id} {t.search} {t.catogories} {t.star} {t.author_name} {t.author_url}"
        )
        last_gid_value = None
        for _ in range(t.query_depth):
            entries, last_gid_value = await client.search_galleries(
                search=t.search,
                catogories=t.catogories,
                star=t.star,
                next_gid=last_gid_value,
            )
            logger.info(f"Found {len(entries)} galleries")
            for e in entries:
                logger.info(f"Parsing gallery: {e.gid} {e.title}")
                gallery = await parse_url(
                    e.url,
                    send_if_exists=False,
                    author_name=t.author_name,
                    author_url=t.author_url,
                )
                if gallery is not None:
                    logger.info(f"Sending gallery: {e.gid} {e.title}")
                    try:
                        await context.bot.send_message(
                            chat_id=t.chat_id,
                            text=generate_telegraph_message(gallery),
                        )
                    except BadRequest as e:
                        if (
                            "Not enough rights to send" in e.message
                            or "Need administrator rights in the channel chat"
                            in e.message
                        ):
                            await delete_task(t.chat_id)
                            logger.warning(f"{e} not enough rights to send to {t.chat_id}, deleted task")
                            try:
                                await context.bot.leave_chat(t.chat_id)
                            except Exception:
                                pass
                            break


def generate_telegraph_message(gallery: Gallery) -> str:
    final = f"[{escape_markdown(gallery.title, 2)}]({gallery.telegraph_url})"
    for k, v in gallery.tags.items():
        final += f"\n{escape_markdown(k, 2)}: \\#{' \\#'.join(escape_markdown(v, 2) for v in v)}"
    final += f"\n原始链接：{escape_markdown(gallery.url, 2)}"
    return final


async def parse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, urls = await message_to_urls(update, context)
    if message is not None and urls:
        for url in urls:
            try:
                await message.reply_chat_action(ChatAction.TYPING)
            except Exception:
                pass
            gallery = await parse_url(
                url,
                author_name=settings.telegraph_author_name,
                author_url=settings.telegraph_author_url,
            )
            if gallery is not None:
                await message.reply_text(generate_telegraph_message(gallery))
            else:
                await message.reply_text(f"解析失败：{escape_markdown(url, 2)}")
    else:
        if message is not None and message.text.startswith("/parse"):
            await message.reply_text("参数不正确，例如：/parse <url\\>")


async def message_to_urls(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[Message | None, list[Any]]:
    message = update.message or update.channel_post
    if message is None:
        return message, []
    if isinstance(message.forward_origin, MessageOriginUser):
        if (
            message.forward_origin.sender_user.is_bot
            and message.forward_origin.sender_user.username == context.bot.username
        ):
            return message, []
    elif isinstance(message.forward_origin, MessageOriginHiddenUser):
        if message.forward_origin.sender_user_name == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, MessageOriginChat):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
    elif isinstance(message.forward_origin, MessageOriginChannel):
        if message.forward_origin.author_signature == context.bot.first_name:
            return message, []
        else:
            ## If the bot is in the channel, return
            try:
                self_user = await message.forward_origin.chat.get_member(context.bot.id)
                ## Bot must be an administrator to access the member list.
                if self_user.status == "administrator":
                    return message, []
            except Exception:
                ## Member list is inaccessible.
                pass
    urls = re.findall(EHENTAI_URL_REGEX, message.text or message.caption or "")
    if message.entities:
        for entity in message.entities:
            if entity.url:
                urls.extend(re.findall(EHENTAI_URL_REGEX, entity.url))
    return message, urls


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "欢迎使用本 Bot，请使用 /parse <url\\> 解析链接"
    )


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text.replace("/add_task ", "")
    try:
        task_data = TaskData.from_text(text)
        await upsert_task(
            chat_id=update.effective_chat.id,
            search=task_data.search,
            catogories=task_data.catogories,
            star=task_data.star,
            author_name=task_data.author_name or update.effective_chat.username,
            author_url=task_data.author_url or update.effective_chat.link,
            query_depth=task_data.query_depth,
        )
        reply = await update.effective_message.reply_text("任务添加成功")
        await update.effective_message.delete()
        await reply.delete()
    except ValueError:
        await update.effective_message.reply_text("参数错误")


async def clear_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await delete_task(update.effective_chat.id)
    reply = await update.effective_message.reply_text("任务清除成功")
    await update.effective_message.delete()
    await reply.delete()


async def post_init(application: Application) -> None:
    await db_init(settings.db_url)
    await application.bot.set_my_commands(
        [
            ["parse", "获取匹配内容"],
        ]
    )
    bot_me = await application.bot.get_me()
    logger.info(f"Bot @{bot_me.username} started.")


async def post_shutdown(application: Application) -> None:
    await client.aclose()
    await catbox.aclose()
    await telegraph.aclose()
    await ehtag.aclose()
    await db_close()


def main() -> None:
    application = (
        Application.builder()
        .defaults(
            Defaults(
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_notification=True,
                allow_sending_without_reply=True,
                block=False,
            )
        )
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .base_url(settings.telegram_api_base_url)
        .base_file_url(settings.telegram_api_base_file_url)
        .local_mode(settings.telegram_local_mode)
        .concurrent_updates(
            int(settings.telegram_semaphore_size)
            if settings.telegram_semaphore_size
            else True
        )
        .build()
    )
    application.add_handler(
        MessageHandler(filters.Regex("^/add_task"), add_task, block=False)
    )
    application.add_handler(
        MessageHandler(filters.Regex("^/clear_task"), clear_task, block=False)
    )
    application.add_handler(CommandHandler("parse", parse, block=False))
    application.add_handler(
        MessageHandler(
            filters.Entity(MessageEntity.URL)
            | filters.Entity(MessageEntity.TEXT_LINK)
            | filters.Regex(EHENTAI_URL_REGEX)
            | filters.CaptionRegex(EHENTAI_URL_REGEX),
            parse,
            block=False,
        )
    )
    application.job_queue.run_repeating(
        job_process, interval=settings.telegram_job_interval, first=30
    )
    if settings.telegram_domain:
        application.run_webhook(
            listen=settings.telegram_host,
            port=int(settings.telegram_port),
            url_path=settings.telegram_bot_token,
            webhook_url=f"{settings.telegram_domain}{settings.telegram_bot_token}",
            max_connections=100,
        )
    else:
        application.run_polling()


if __name__ == "__main__":
    main()
