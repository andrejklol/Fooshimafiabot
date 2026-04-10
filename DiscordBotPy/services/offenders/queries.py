from .storage import repeat_offenders


def get_repeat_offenders():

    return repeat_offenders


def format_repeat_offenders():

    lines = []

    for user in repeat_offenders.values():

        lines.append(
            f"{user['name']} | warns={user['warn']} kicks={user['kick']} bans={user['ban']}"
        )

    return lines