from .storage import repeat_offenders


def get_repeat_offenders() -> dict:
    return repeat_offenders


def format_repeat_offenders() -> list[str]:
    if not repeat_offenders:
        return ["No repeat offenders recorded."]

    lines: list[str] = []

    for user_id, user in repeat_offenders.items():
        if not isinstance(user, dict):
            lines.append(f"{user_id} | invalid offender data")
            continue

        name = str(user.get("name", "Unknown"))
        warns = int(user.get("warn", 0) or 0)
        kicks = int(user.get("kick", 0) or 0)
        bans = int(user.get("ban", 0) or 0)

        lines.append(
            f"{name} | warns={warns} kicks={kicks} bans={bans}"
        )

    return lines
