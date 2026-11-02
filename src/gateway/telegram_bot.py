import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()
logger = logging.getLogger(__name__)

TopicsConfirmedCallback = Callable[[int, list[str]], Awaitable[None]]


class TelegramTrendBot:
    def __init__(
        self,
        token: str | None = None,
        user_id: int | None = None,
        *,
        on_topics_confirmed: TopicsConfirmedCallback,
    ):
        self.token = token or os.getenv("BOT_TOKEN")
        if not self.token:
            raise RuntimeError("BOT_TOKEN environment variable is not set.")

        self.user_id = user_id if user_id is not None else int(os.getenv("USER_ID", "0"))
        if not self.user_id:
            raise RuntimeError("USER_ID environment variable is not set.")

        self.app = ApplicationBuilder().token(self.token).build()
        self.selected_trends: dict[int, list[str]] = {}
        self.digest_trends: dict[int, list[Any]] = {}
        self.on_topics_confirmed = on_topics_confirmed
        self.add_handlers()

    def is_authorized(self, update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id == self.user_id)

    @staticmethod
    def _trend_title(trend: Any) -> str:
        if isinstance(trend, str):
            return trend
        if isinstance(trend, dict):
            return str(trend.get("title") or trend.get("topic") or "Unknown topic")
        return str(trend)

    def _build_keyboard(self, chat_id: int, trends: list[Any]) -> InlineKeyboardMarkup:
        selected = self.selected_trends.get(chat_id, [])
        buttons: list[list[InlineKeyboardButton]] = []

        for index, trend in enumerate(trends[:10]):
            title = self._trend_title(trend)
            label = title[:30] + "..." if len(title) > 30 else title
            if title in selected:
                label = f"✅ {label}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"toggle:{index}")])

        buttons.append([InlineKeyboardButton("Confirm selection", callback_data="confirm")])
        return InlineKeyboardMarkup(buttons)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_authorized(update):
            await update.message.reply_text("You are not authorized to use this bot.")
            return
        await update.message.reply_text(
            "Hello! I'm your bot. Use /help to see available commands.\n"
            "I can also send a trend digest with selectable topics."
        )

    async def send_digest_to_chat(self, chat_id: int, trends: list[Any]) -> None:
        if chat_id <= 0:
            raise ValueError("chat_id must be a valid Telegram chat id.")

        self.digest_trends[chat_id] = trends
        self.selected_trends.setdefault(chat_id, [])

        bullet_list = "\n".join(f"• {self._trend_title(item)}" for item in trends[:10])
        message = "Top trends for today:\n\n" + bullet_list
        keyboard = self._build_keyboard(chat_id, trends)

        await self.app.bot.send_message(chat_id=chat_id, text=message, reply_markup=keyboard)

    async def send_telegram_digest(self, trends: list[Any], update: Update | None, chat_id: int | None = None) -> None:
        if update is not None and not self.is_authorized(update):
            return

        if chat_id is None:
            if update is not None and getattr(update, "effective_chat", None) is not None:
                chat_id = update.effective_chat.id
            elif update is not None and getattr(update, "message", None) is not None:
                chat_id = update.message.chat_id
            else:
                raise ValueError("chat_id is required when no update object is passed.")

        await self.send_digest_to_chat(chat_id, trends)

    async def handle_trend_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_authorized(update):
            return

        query = update.callback_query
        if query is None:
            return

        await query.answer()
        chat_id = query.message.chat_id
        data = query.data or ""
        selected = self.selected_trends.setdefault(chat_id, [])
        trends = self.digest_trends.get(chat_id, [])

        if data == "confirm":
            if not selected:
                await query.edit_message_text("No trends selected yet. Tap the topics you want to include.")
                return

            topics = selected.copy()
            self.selected_trends[chat_id] = []
            selected_text = "\n".join(f"• {topic}" for topic in topics)
            await query.edit_message_text(
                "Confirmed topics; starting script generation:\n\n" + selected_text
            )
            context.application.create_task(
                self.on_topics_confirmed(chat_id, topics),
                update=update,
            )
            return

        if data.startswith("toggle:"):
            try:
                index = int(data.split(":", 1)[1])
            except ValueError:
                return

            if index >= len(trends):
                return

            title = self._trend_title(trends[index])
            if title in selected:
                selected.remove(title)
            else:
                selected.append(title)

            await query.edit_message_reply_markup(reply_markup=self._build_keyboard(chat_id, trends))

    def add_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CallbackQueryHandler(self.handle_trend_selection))

    def run(self) -> None:
        self.app.run_polling()