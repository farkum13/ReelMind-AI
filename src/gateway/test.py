import os
from telegram import Bot

token = os.getenv("BOT_TOKEN")
chat_id = int(os.getenv("CHAT_ID"))

async def main():
	bot = Bot(token=token)
	await bot.send_message(chat_id=chat_id, text="Hello from my Python shell ✅")

if __name__ == "__main__":
	import asyncio
	asyncio.run(main())