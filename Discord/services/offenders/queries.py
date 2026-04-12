from .storage import repeat_offenders


def get_repeat_offenders():
    return repeat_offenders


def format_repeat_offenders():

    lines = []

    if not repeat_offenders:
        return ["No repeat offenders recorded."]

    for user_id, user in repeat_offenders.items():

        name = user.get("name", "Unknown")

        warns = user.get("warn", 0)
        kicks = user.get("kick", 0)
        bans = user.get("ban", 0)

        lines.append(
            f"{name} | warns={warns} kicks={kicks} bans={bans}"
        )

    return lines
