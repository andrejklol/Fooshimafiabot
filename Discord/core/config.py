import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)

LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"
REPEAT_OFFENDER_FILE = DATA_DIR / "repeat_offenders.json"

# ============================================================
# DISCORD / VRCHAT CORE
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

GROUP_ID = os.getenv(
    "VRCHAT_GROUP_ID",
    "grp_23595db4-1452-4fbf-97e1-661fa1b9b074"
)

VRCHAT_USERNAME = os.getenv("VRCHAT_USERNAME")
VRCHAT_PASSWORD = os.getenv("VRCHAT_PASSWORD")
VRCHAT_EMAIL_OTP = os.getenv("VRCHAT_EMAIL_OTP")

# ============================================================
# OWNER / ACCESS CONTROL
# ============================================================

OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "899310036965294131"))

RESET_ALLOWED_USER_ID = OWNER_USER_ID
SYNC_ALLOWED_USER_ID = OWNER_USER_ID
LOAD_HISTORY_ALLOWED_USER_ID = OWNER_USER_ID

# ============================================================
# CHANNELS
# ============================================================

ERROR_LOG_CHANNEL_ID = int(os.getenv("ERROR_LOG_CHANNEL_ID", "1489723749988175922"))
REPEAT_ALERT_CHANNEL_ID = int(os.getenv("REPEAT_ALERT_CHANNEL_ID", "1470118845137289266"))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "1487924213699448903"))

# ============================================================
# LEADERBOARD SETTINGS
# ============================================================

TOP_LIMIT = int(os.getenv("TOP_LIMIT", "3"))

WARN_SCORE = int(os.getenv("WARN_SCORE", "1"))
KICK_SCORE = int(os.getenv("KICK_SCORE", "2"))
BAN_SCORE = int(os.getenv("BAN_SCORE", "4"))
INVITE_SCORE = int(os.getenv("INVITE_SCORE", "0"))
INVITE_ACCEPT_BONUS = int(os.getenv("INVITE_ACCEPT_BONUS", "1"))

# ============================================================
# POLLING / HISTORY / SAVE SETTINGS
# ============================================================

LOG_POLL_MINUTES = int(os.getenv("LOG_POLL_MINUTES", "1"))

RECENT_LOG_FETCH_COUNT = int(os.getenv("RECENT_LOG_FETCH_COUNT", "10"))
SEED_LOG_FETCH_COUNT = int(os.getenv("SEED_LOG_FETCH_COUNT", "100"))

HISTORY_BATCH_SIZE = int(os.getenv("HISTORY_BATCH_SIZE", "100"))
MAX_HISTORY_LOAD = int(os.getenv("MAX_HISTORY_LOAD", "5000"))

AUTOSAVE_SECONDS = int(os.getenv("AUTOSAVE_SECONDS", "30"))

# ============================================================
# REPEAT OFFENDER SETTINGS
# ============================================================

REPEAT_WARN_THRESHOLD = int(os.getenv("REPEAT_WARN_THRESHOLD", "3"))
REPEAT_KICK_THRESHOLD = int(os.getenv("REPEAT_KICK_THRESHOLD", "2"))
REPEAT_BAN_THRESHOLD = int(os.getenv("REPEAT_BAN_THRESHOLD", "1"))

REPEAT_WARN_WINDOW_DAYS = int(os.getenv("REPEAT_WARN_WINDOW_DAYS", "7"))
REPEAT_KICK_WINDOW_DAYS = int(os.getenv("REPEAT_KICK_WINDOW_DAYS", "30"))
REPEAT_BAN_WINDOW_DAYS = int(os.getenv("REPEAT_BAN_WINDOW_DAYS", "30"))

# ============================================================
# VRCHAT STAFF ROLE NAMES
# ============================================================

VRC_STAFF_ROLE_NAMES = {

    "godfooshi",

    "fooshi underboss",

    "fooshi consigliere",

    "fooshi capo",

    "fooshi soldier",
}

# ============================================================
# STAFF ALERT ORDER
# ============================================================

STAFF_ALERT_ORDER = {

    "warn": [

        (
            "Fooshi Soldier",
            [

                {
                    "discord_id": 388783101184180224,
                    "vrchat_user_id": "usr_8c95ece0-3e42-4b78-9036-5fc75aa5ca3c"
                },

                {
                    "discord_id": 924925123184717904,
                    "vrchat_user_id": "usr_83b26675-1275-446a-a587-57c66c968170"
                },

                {
                    "discord_id": 1344857878284075031,
                    "vrchat_user_id": "usr_3806653f-d199-475c-aca0-b17826d84964"
                },

            ],
        ),

        (
            "Fooshi Capo",
            [
                {
                    "discord_id": 697602081233829975,
                    "vrchat_user_id": "usr_b1431dfd-201a-476f-8cbc-3908527cc370"
                },
                {
                    "discord_id": 933075890194235402,
                    "vrchat_user_id": "usr_9252b0c1-586d-47c8-8282-314a30ef3eac"
                },
            ],
        ),

        (
            "Fooshi Consigliere",
            [
                {
                    "discord_id": 862857344286326864,
                    "vrchat_user_id": "usr_1560e59a-983c-4802-90ee-88963b1893dd"
                },
                {
                    "discord_id": 1096271363007840369,
                    "vrchat_user_id": "usr_d19a8408-8991-4822-88f5-38093ccb4620"
                },
            ],
        ),

        (
            "Fooshi Underboss",
            [
                {
                    "discord_id": 638482686612078614,
                    "vrchat_user_id": "usr_3d664c85-ce46-4441-9a92-5a58946098c3"
                },
            ],
        ),

        (
            "Godfooshi",
            [
                {
                    "discord_id": 899310036965294131,
                    "vrchat_user_id": "usr_e7f83a47-a121-428d-a79b-72ae1c618705"
                },
            ],
        ),
    ],

    "kick": [

        (
            "Fooshi Capo",
            [
                {
                    "discord_id": 697602081233829975,
                    "vrchat_user_id": "usr_b1431dfd-201a-476f-8cbc-3908527cc370"
                },
                {
                    "discord_id": 933075890194235402,
                    "vrchat_user_id": "usr_9252b0c1-586d-47c8-8282-314a30ef3eac"
                },
            ],
        ),

        (
            "Fooshi Consigliere",
            [
                {
                    "discord_id": 862857344286326864,
                    "vrchat_user_id": "usr_1560e59a-983c-4802-90ee-88963b1893dd"
                },
                {
                    "discord_id": 1096271363007840369,
                    "vrchat_user_id": "usr_d19a8408-8991-4822-88f5-38093ccb4620"
                },
            ],
        ),

        (
            "Fooshi Underboss",
            [
                {
                    "discord_id": 638482686612078614,
                    "vrchat_user_id": "usr_3d664c85-ce46-4441-9a92-5a58946098c3"
                },
            ],
        ),

        (
            "Godfooshi",
            [
                {
                    "discord_id": 899310036965294131,
                    "vrchat_user_id": "usr_e7f83a47-a121-428d-a79b-72ae1c618705"
                },
            ],
        ),
    ],

    "ban": [

        (
            "Fooshi Consigliere",
            [
                {
                    "discord_id": 862857344286326864,
                    "vrchat_user_id": "usr_1560e59a-983c-4802-90ee-88963b1893dd"
                },
                {
                    "discord_id": 1096271363007840369,
                    "vrchat_user_id": "usr_d19a8408-8991-4822-88f5-38093ccb4620"
                },
            ],
        ),

        (
            "Fooshi Underboss",
            [
                {
                    "discord_id": 638482686612078614,
                    "vrchat_user_id": "usr_3d664c85-ce46-4441-9a92-5a58946098c3"
                },
            ],
        ),

        (
            "Godfooshi",
            [
                {
                    "discord_id": 899310036965294131,
                    "vrchat_user_id": "usr_e7f83a47-a121-428d-a79b-72ae1c618705"
                },
            ],
        ),
    ],
}

# ============================================================
# COMMAND SYNC
# ============================================================

SYNC_COMMANDS_ON_STARTUP = os.getenv(
    "SYNC_COMMANDS_ON_STARTUP",
    "false"
).lower() == "true"


# ============================================================
# HIGH STAFF ALERT SETTINGS
# ============================================================

HIGH_STAFF_ALERT_ENABLED = os.getenv(
    "HIGH_STAFF_ALERT_ENABLED",
    "true"
).lower() == "true"

HIGH_STAFF_ALERT_CHANNEL_ID = int(
    os.getenv(
        "HIGH_STAFF_ALERT_CHANNEL_ID",
        str(ALERT_CHANNEL_ID)
    )
)

HIGH_STAFF_WARN_THRESHOLD = int(
    os.getenv("HIGH_STAFF_WARN_THRESHOLD", "8")
)

HIGH_STAFF_KICK_THRESHOLD = int(
    os.getenv("HIGH_STAFF_KICK_THRESHOLD", "5")
)

HIGH_STAFF_BAN_THRESHOLD = int(
    os.getenv("HIGH_STAFF_BAN_THRESHOLD", "3")
)

HIGH_STAFF_WINDOW_MINUTES = int(
    os.getenv("HIGH_STAFF_WINDOW_MINUTES", "10")
)

HIGH_STAFF_ALERT_COOLDOWN_MINUTES = int(
    os.getenv("HIGH_STAFF_ALERT_COOLDOWN_MINUTES", "30")
)


# ============================================================
# SUSPICIOUS MOD DETECTOR SETTINGS
# ============================================================

SUSPICIOUS_MOD_ENABLED = os.getenv(
    "SUSPICIOUS_MOD_ENABLED",
    "true"
).lower() == "true"

SUSPICIOUS_UNIQUE_TARGET_THRESHOLD = int(
    os.getenv("SUSPICIOUS_UNIQUE_TARGET_THRESHOLD", "6")
)

SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES = int(
    os.getenv("SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES", "10")
)

SUSPICIOUS_REPEAT_TARGET_THRESHOLD = int(
    os.getenv("SUSPICIOUS_REPEAT_TARGET_THRESHOLD", "4")
)

SUSPICIOUS_REPEAT_TARGET_WINDOW_MINUTES = int(
    os.getenv("SUSPICIOUS_REPEAT_TARGET_WINDOW_MINUTES", "10")
)

SUSPICIOUS_ALERT_COOLDOWN_MINUTES = int(
    os.getenv("SUSPICIOUS_ALERT_COOLDOWN_MINUTES", "30")
)
