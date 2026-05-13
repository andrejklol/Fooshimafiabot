from pathlib import Path

STAFF_ROLES = {
    "godfooshi":          [{"discord_id": 899310036965294131}],
    "fooshi underboss":   [{"discord_id": 638482686612078614}],
    "fooshi consigliere": [
        {"discord_id": 862857344286326864},
        {"discord_id": 1096271363007840369},
    ],
    "fooshi capo": [
        {"discord_id": 697602081233829975},
        {"discord_id": 933075890194235402},
    ],
    "fooshi soldier": [
        {"discord_id": 388783101184180224},
        {"discord_id": 1016188813166526505},
        {"discord_id": 1344857878284075031},
        {"discord_id": 1342000376806768731},
    ],
}

VIP_ROLE_ID             = 1503212632709267457
VIP_USER_ID             = 1256744656931131429
NEW_MEMBER_CHANNEL_ID   = 1470118124807520346
DAILY_REPORT_CHANNEL_ID = 1470118124807520346
SERVER_ANNIVERSARY      = (1, 1)
MEMBER_MILESTONES       = {100, 200, 300, 500, 750, 1000}
WITNESS_PROTECTION: set[int] = set()
RETIREMENT_HOUR_THRESHOLD = 6
CONVERSATION_TIMEOUT = 600
MAX_HISTORY_TURNS = 8

MEMBER_PRONOUNS: dict[int, str] = {
    899310036965294131: "m",
    638482686612078614: "m",
    862857344286326864: "f",
    1096271363007840369: "m",
    697602081233829975: "m",
    933075890194235402: "f",
    388783101184180224: "f",
    1016188813166526505: "m",
    1344857878284075031: "m",
    1342000376806768731: "f",
    1256744656931131429: "f",
}

GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
SAL_STATE_FILE = Path(__file__).parent.parent.parent / "data" / "sal_state.json"

CHANNEL_IDS = {
    "general":        1470114217075151022,
    "media":          1470117293634621552,
    "art":            1488998734569148486,
    "command_office": 1470117536325566727,
    "inner_circle":   1487924213699448903,
}

CHANNEL_VIBES = {
    1470114217075151022: "Sal is in the general area. He has mopped this room more than anywhere else. Slightly more relaxed but always watching.",
    1470117293634621552: "Sal wandered into the media room. He does not understand what is happening here. He is confused but professionally obligated to answer.",
    1488998734569148486: "Sal is in the art room. He pretends he has no opinions. He has very strong opinions. He will not volunteer them.",
    1470117536325566727: "Sal is in the command office. He says as little as possible. He knows everything said in this room stays in this room.",
    1487924213699448903: "Sal is in the inner circle. He barely exists here. Minimum words. Maximum care. He has heard things in this room that he will take to the grave.",
}
