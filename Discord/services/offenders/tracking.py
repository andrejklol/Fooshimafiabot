from .storage import repeat_offenders, save_repeat_offenders


WARN_THRESHOLD = 3
KICK_THRESHOLD = 2
BAN_THRESHOLD = 1

WARN_WINDOW_DAYS = 7
KICK_WINDOW_DAYS = 30
BAN_WINDOW_DAYS = 30


def _ensure_user(user_id: str, username: str) -> None:
    if user_id not in repeat_offenders:
        repeat_offenders[user_id] = {
            "name": username,
            "warn": 0,
            "kick": 0,
            "ban": 0,
        }
    else:
        repeat_offenders[user_id]["name"] = username


def add_warn(user_id: str, username: str) -> dict:
    _ensure_user(user_id, username)
    repeat_offenders[user_id]["warn"] += 1
    save_repeat_offenders()
    return repeat_offenders[user_id]


def add_kick(user_id: str, username: str) -> dict:
    _ensure_user(user_id, username)
    repeat_offenders[user_id]["kick"] += 1
    save_repeat_offenders()
    return repeat_offenders[user_id]


def add_ban(user_id: str, username: str) -> dict:
    _ensure_user(user_id, username)
    repeat_offenders[user_id]["ban"] += 1
    save_repeat_offenders()
    return repeat_offenders[user_id]


def get_repeat_offender_data(user_id: str) -> dict | None:
    data = repeat_offenders.get(user_id)
    return data if isinstance(data, dict) else None


def get_triggered_thresholds(user_id: str) -> list[tuple[str, int, int, int]]:
    data = get_repeat_offender_data(user_id)

    if not data:
        return []

    triggered: list[tuple[str, int, int, int]] = []

    warn_count = int(data.get("warn", 0) or 0)
    kick_count = int(data.get("kick", 0) or 0)
    ban_count = int(data.get("ban", 0) or 0)

    if warn_count >= WARN_THRESHOLD:
        triggered.append(("warn", warn_count, WARN_WINDOW_DAYS, WARN_THRESHOLD))

    if kick_count >= KICK_THRESHOLD:
        triggered.append(("kick", kick_count, KICK_WINDOW_DAYS, KICK_THRESHOLD))

    if ban_count >= BAN_THRESHOLD:
        triggered.append(("ban", ban_count, BAN_WINDOW_DAYS, BAN_THRESHOLD))

    return triggered


def get_highest_action(triggered: list[tuple[str, int, int, int]]) -> str:
    actions = {action for action, *_ in triggered}

    if "ban" in actions:
        return "ban"

    if "kick" in actions:
        return "kick"

    if "warn" in actions:
        return "warn"

    return ""


def is_repeat_offender(user_id: str) -> bool:
    return bool(get_triggered_thresholds(user_id))
