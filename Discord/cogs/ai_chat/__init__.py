import logging
from .cog import AIChat, _GENAI_AVAILABLE

log = logging.getLogger(__name__)


async def setup(bot):
    if not _GENAI_AVAILABLE:
        log.warning("[ai_chat] google-genai not installed — run: pip install google-genai")
        return
    cog = AIChat(bot)
    await bot.add_cog(cog)
    cog.daily_report_task.start()
    cog.meeting_minutes_task.start()
    cog.autosave_task.start()
