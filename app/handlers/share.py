from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent

from app.services.proxy_links import ProxyStore, build_share_text
from app.services.storage import Storage

router = Router()


@router.inline_query()
async def inline_share(
    inline_query: InlineQuery,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
) -> None:
    user = inline_query.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    proxies = proxy_store.load_enabled()
    if not proxies:
        await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    id="no_proxy",
                    title="Прокси временно недоступен",
                    description="Нажмите, чтобы отправить контакт поддержки",
                    input_message_content=InputTextMessageContent(
                        message_text=f"Сейчас прокси временно недоступен. Поддержка: https://t.me/{support_username}",
                        disable_web_page_preview=True,
                    ),
                )
            ],
            cache_time=3,
            is_personal=True,
        )
        return

    results: list[InlineQueryResultArticle] = []
    for idx, proxy in enumerate(proxies[:20]):
        results.append(
            InlineQueryResultArticle(
                id=f"proxy_{idx}",
                title=f"{proxy.name} - MTProto proxy",
                description=f"{proxy.server}:{proxy.port}",
                input_message_content=InputTextMessageContent(
                    message_text=build_share_text(proxy),
                    disable_web_page_preview=True,
                ),
            )
        )

    await inline_query.answer(results=results, cache_time=10, is_personal=True)
