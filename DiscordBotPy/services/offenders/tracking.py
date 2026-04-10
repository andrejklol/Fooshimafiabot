from .storage import repeat_offenders


def _ensure_user(user_id: str, username: str):

    if user_id not in repeat_offenders:

        repeat_offenders[user_id] = {
            "name": username,
            "warn": 0,
            "kick": 0,
            "ban": 0,
        }


def add_warn(user_id: str, username: str):

    _ensure_user(user_id, username)

    repeat_offenders[user_id]["warn"] += 1


def add_kick(user_id: str, username: str):

    _ensure_user(user_id, username)

    repeat_offenders[user_id]["kick"] += 1


def add_ban(user_id: str, username: str):

    _ensure_user(user_id, username)

    repeat_offenders[user_id]["ban"] += 1


def is_repeat_offender(user_id: str):

    data = repeat_offenders.get(user_id)

    if not data:
        return False

    return data["warn"] >= 3 or data["kick"] >= 2 or data["ban"] >= 1