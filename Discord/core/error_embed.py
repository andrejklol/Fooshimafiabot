import traceback
from datetime import UTC, datetime

import discord


def _resolve_style(level: str) -> tuple[str, discord.Color]:
    level = (level or "error").lower()

    if level == "debug":
        return "ℹ️", discord.Color.blue()

    if level == "info":
        return "ℹ️", discord.Color.blurple()

    if level == "warning":
        return "⚠️", discord.Color.yellow()

    if level == "success":
        return "✅", discord.Color.green()

    return "❌", discord.Color.red()


def _stringify_error(error) -> str:
    if error is None:
        return "Unknown error"

    if isinstance(error, BaseException):
        try:
            return "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ).strip()
        except Exception:
            return f"{type(error).__name__}: {error}"

    return str(error)


def _truncate(text: str, limit: int = 4000) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _chunk_text(text: str, size: int = 1000) -> list[str]:
    text = str(text or "")
    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        chunks.append(text[start:start + size])
        start += size

    return chunks


def build_error_embed(
        *,
        title: str = "Error",
        description: str = "Something went wrong.",
        username: str | None = None,
        actor_id: str | None = None,
        trace_id: str | None = None,
        extra: dict | None = None,
        level: str = "error",
) -> discord.Embed:
    icon, color = _resolve_style(level)

    embed = discord.Embed(
        title=f"{icon} {title}",
        description=_truncate(description, 4000),
        color=color,
        timestamp=datetime.now(UTC),
    )

    if username:
        embed.add_field(
            name="User",
            value=f"`{_truncate(username, 1000)}`",
            inline=True,
        )

    if actor_id:
        embed.add_field(
            name="Actor ID",
            value=f"`{_truncate(actor_id, 1000)}`",
            inline=True,
        )

    if trace_id:
        embed.add_field(
            name="Trace ID",
            value=f"`{_truncate(trace_id, 1000)}`",
            inline=False,
        )

    if extra:
        for key, value in extra.items():
            text = str(value if value is not None else "None")

            chunks = _chunk_text(text, 1000)
            if not chunks:
                chunks = ["None"]

            for index, chunk in enumerate(chunks[:5]):
                field_name = str(key) if index == 0 else f"{key} (cont. {index})"

                if "\n" in chunk:
                    field_value = f"```py\n{chunk}\n```"
                else:
                    field_value = f"`{chunk}`"

                embed.add_field(
                    name=_truncate(field_name, 256),
                    value=_truncate(field_value, 1024),
                    inline=False,
                )

    embed.set_footer(text="Fooshi Error Monitor")
    return embed


async def send_error_embed(
        bot,
        channel_id: int,
        *,
        title: str = "Error",
        description: str = "Something went wrong.",
        username: str | None = None,
        actor_id: str | None = None,
        trace_id: str | None = None,
        extra: dict | None = None,
        level: str = "error",
):
    try:
        if not bot or not channel_id:
            return

        channel = bot.get_channel(channel_id)

        if not channel:
            channel = await bot.fetch_channel(channel_id)

        if not channel or not hasattr(channel, "send"):
            return

        embed = build_error_embed(
            title=title,
            description=description,
            username=username,
            actor_id=actor_id,
            trace_id=trace_id,
            extra=extra,
            level=level,
        )

        await channel.send(embed=embed)

    except Exception as e:
        print(f"Failed to send error embed: {e}")


async def send_exception_embed(
        bot,
        channel_id: int,
        *,
        title: str = "Error",
        error: Exception | None = None,
        username: str | None = None,
        actor_id: str | None = None,
        trace_id: str | None = None,
        extra: dict | None = None,
        level: str = "error",
):
    error_text = _stringify_error(error)

    merged_extra = dict(extra or {})
    if error is not None:
        merged_extra.setdefault("error_type", type(error).__name__)
        merged_extra.setdefault("traceback", error_text)

    await send_error_embed(
        bot,
        channel_id,
        title=title,
        description=f"{type(error).__name__}: {error}" if error else "Unknown error",
        username=username,
        actor_id=actor_id,
        trace_id=trace_id,
        extra=merged_extra,
        level=level,
    )