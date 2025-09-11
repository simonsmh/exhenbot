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
from telegram.constants import ParseMode
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
from .storage import Gallery, db_close, db_init, get_gallery, upsert_gallery
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


async def parse_url(url: str, send_if_exists: bool = True) -> Gallery:
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
        author_name=settings.telegraph_author_name,
        author_url=settings.telegraph_author_url,
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
    entries = await client.search_galleries(
        search=settings.exh_query,
        catogories=settings.exh_catogories,
        star=settings.exh_star,
    )
    logger.info(f"Found {len(entries)} galleries")
    for e in entries:
        logger.info(f"Parsing gallery: {e.gid} {e.title}")
        gallery = await parse_url(e.url, send_if_exists=False)
        if gallery is not None:
            logger.info(f"Sending gallery: {e.gid} {e.title}")
            await context.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=generate_telegraph_message(gallery),
            )


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
            gallery = await parse_url(url)
            if gallery is not None:
                await message.reply_text(generate_telegraph_message(gallery))
            else:
                await message.reply_text(
                    f"Failed to parse URL: {escape_markdown(url, 2)}"
                )


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


async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        escape_markdown(str(update.effective_chat.id), 2)
    )


async def post_init(application: Application) -> None:
    await db_init(settings.db_url)
    await ehtag.load_database()


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
        MessageHandler(filters.Regex("^/id"), get_chat_id, block=False)
    )
    application.add_handler(CommandHandler("id", get_chat_id, block=False))
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
    if settings.telegram_chat_id:
        job_queue = application.job_queue
        job_queue.run_repeating(
            job_process,
            interval=settings.telegram_job_interval,
            first=30,
            chat_id=settings.telegram_chat_id,
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
