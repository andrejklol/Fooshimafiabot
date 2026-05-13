import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Helper for Environment Variables ---
def get_env(key: str, default=None, cast=str):
    val = os.getenv(key)
    if val is None:
        return default
    if cast == bool:
        return str(val).lower() == "true"
    try:
        return cast(val)
    except (ValueError, TypeError):
        return default

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"
REPEAT_OFFENDER_FILE = DATA_DIR / "repeat_offenders.json"

# --- Core Credentials ---
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = get_env("GUILD_ID", 1216885304485478551, int)  # Configured with your server ID
OWNER_USER_ID = get_env("OWNER_USER_ID", 899310036965294131, int)

# --- Staff Definitions ---
STAFF_ROLES = {
    "godfooshi": [
        {"discord_id": 899310036965294131, "vrchat_user_id": "usr_e7f83a47-a121-428d-a79b-72ae1c618705"}
    ],
    "fooshi underboss": [
        {"discord_id": 638482686612078614, "vrchat_user_id": "usr_3d664c85-ce46-4441-9a92-5a58946098c3"}
    ],
    "fooshi consigliere": [
        {"discord_id": 862857344286326864, "vrchat_user_id": "usr_1560e59a-983c-4802-90ee-88963b1893dd"},
        {"discord_id": 1096271363007840369, "vrchat_user_id": "usr_d19a8408-8991-4822-88f5-38093ccb4620"}
    ],
    "fooshi capo": [
        {"discord_id": 697602081233829975, "vrchat_user_id": "usr_b1431dfd-201a-476f-8cbc-3908527cc370"},
        {"discord_id": 933075890194235402, "vrchat_user_id": "usr_9252b0c1-586d-47c8-8282-314a30ef3eac"}
    ],
    "fooshi soldier": [
        {"discord_id": 388783101184180224, "vrchat_user_id": "usr_8c95ece0-3e42-4b78-9036-5fc75aa5ca3c"},
        {"discord_id": 1016188813166526505, "vrchat_user_id": "usr_737702d3-4230-4aaa-a9d2-b9fe30be427c"},
        {"discord_id": 1344857878284075031, "vrchat_user_id": "usr_3806653f-d199-475c-aca0-b17826d84964"},
        {"discord_id": 1342000376806768731, "vrchat_user_id": "usr_6c26862f-232a-4662-890e-6a6b34569484"}
    ],
}

# Restored as a global variable so other files can import it!
VRC_STAFF_ROLE_NAMES = list(STAFF_ROLES.keys())

# --- VRChat Configuration ---
VRC_CONFIG = {
    "username": os.getenv("VRCHAT_USERNAME"),
    "password": os.getenv("VRCHAT_PASSWORD"),
    "otp": os.getenv("VRCHAT_EMAIL_OTP"),
    "group_id": os.getenv("VRCHAT_GROUP_ID", "grp_23595db4-1452-4fbf-97e1-661fa1b9b074"),
    "staff_role_names": VRC_STAFF_ROLE_NAMES 
}

# --- Channels ---
CHANNELS = {
    "log": get_env("LOG_CHANNEL_ID", 0, int),
    "error": get_env("ERROR_LOG_CHANNEL_ID", 1489723749988175922, int),
    "alert": get_env("ALERT_CHANNEL_ID", 1487924213699448903, int),
    "repeat": get_env("REPEAT_ALERT_CHANNEL_ID", 1470118845137289266, int),
}

# --- Scoring & Thresholds ---
SCORES = {
    "warn": get_env("WARN_SCORE", 1, int),
    "kick": get_env("KICK_SCORE", 2, int),
    "ban": get_env("BAN_SCORE", 4, int),
    "invite": get_env("INVITE_SCORE", 0, int),
    "invite_bonus": get_env("INVITE_ACCEPT_BONUS", 1, int),
}

REPEAT_OFFENDER = {
    "thresholds": {
        "warn": get_env("REPEAT_WARN_THRESHOLD", 3, int),
        "kick": get_env("REPEAT_KICK_THRESHOLD", 2, int),
        "ban": get_env("REPEAT_BAN_THRESHOLD", 1, int)
    },
    "windows": {
        "warn": get_env("REPEAT_WARN_WINDOW_DAYS", 7, int),
        "kick": get_env("REPEAT_KICK_WINDOW_DAYS", 30, int),
        "ban": get_env("REPEAT_BAN_WINDOW_DAYS", 30, int)
    }
}

# --- Build Alert Hierarchy ---
STAFF_ALERT_ORDER = {
    "warn": [ (k, STAFF_ROLES[k]) for k in reversed(list(STAFF_ROLES.keys())) ],
    "kick": [ (k, STAFF_ROLES[k]) for k in list(STAFF_ROLES.keys()) if k != "fooshi soldier" ],
    "ban":  [ (k, STAFF_ROLES[k]) for k in ["fooshi consigliere", "fooshi underboss", "godfooshi"] ],
}

# --- Polling & History ---
LOG_POLL_MINUTES = get_env("LOG_POLL_MINUTES", 1, int)
AUTOSAVE_SECONDS = get_env("AUTOSAVE_SECONDS", 30, int)
TOP_LIMIT = get_env("TOP_LIMIT", 3, int)

# --- Feature Flags ---
SYNC_COMMANDS_ON_STARTUP = get_env("SYNC_COMMANDS_ON_STARTUP", True, bool)

HIGH_STAFF_ALERT = {
    "enabled": get_env("HIGH_STAFF_ALERT_ENABLED", True, bool),
    "channel": get_env("HIGH_STAFF_ALERT_CHANNEL_ID", CHANNELS["alert"], int),
    "thresholds": {
        "warn": get_env("HIGH_STAFF_WARN_THRESHOLD", 8, int),
        "kick": get_env("HIGH_STAFF_KICK_THRESHOLD", 5, int),
        "ban": get_env("HIGH_STAFF_BAN_THRESHOLD", 3, int)
    },
    "window_min": get_env("HIGH_STAFF_WINDOW_MINUTES", 10, int),
    "cooldown_min": get_env("HIGH_STAFF_ALERT_COOLDOWN_MINUTES", 30, int),
}

MOD_DETECTOR = {
    "enabled": get_env("SUSPICIOUS_MOD_ENABLED", True, bool),
    "unique_target_threshold": get_env("SUSPICIOUS_UNIQUE_TARGET_THRESHOLD", 6, int),
    "repeat_target_threshold": get_env("SUSPICIOUS_REPEAT_TARGET_THRESHOLD", 4, int),
    "window_min": get_env("SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES", 10, int),
    "cooldown_min": get_env("SUSPICIOUS_ALERT_COOLDOWN_MINUTES", 30, int),
}

# ============================================================
# LEGACY EXPORTS
# ============================================================
ERROR_LOG_CHANNEL_ID = CHANNELS["error"]
REPEAT_ALERT_CHANNEL_ID = CHANNELS["repeat"]
ALERT_CHANNEL_ID = CHANNELS["alert"]
LOG_CHANNEL_ID = CHANNELS["log"]
HIGH_STAFF_ALERT_CHANNEL_ID = HIGH_STAFF_ALERT["channel"]

import logging as _logging
_logging.getLogger(__name__).debug("Config loaded — GUILD_ID=%s", GUILD_ID)

GROUP_ID = VRC_CONFIG["group_id"]
VRCHAT_GROUP_ID = VRC_CONFIG["group_id"]
VRCHAT_USERNAME = VRC_CONFIG["username"]
VRCHAT_PASSWORD = VRC_CONFIG["password"]
VRCHAT_EMAIL_OTP = VRC_CONFIG["otp"]

WARN_SCORE = SCORES["warn"]
KICK_SCORE = SCORES["kick"]
BAN_SCORE = SCORES["ban"]
INVITE_SCORE = SCORES["invite"]
INVITE_ACCEPT_BONUS = SCORES["invite_bonus"]

REPEAT_WARN_THRESHOLD = REPEAT_OFFENDER["thresholds"]["warn"]
REPEAT_KICK_THRESHOLD = REPEAT_OFFENDER["thresholds"]["kick"]
REPEAT_BAN_THRESHOLD = REPEAT_OFFENDER["thresholds"]["ban"]

RECENT_LOG_FETCH_COUNT = get_env("RECENT_LOG_FETCH_COUNT", 10, int)
SEED_LOG_FETCH_COUNT = get_env("SEED_LOG_FETCH_COUNT", 100, int)
HISTORY_BATCH_SIZE = get_env("HISTORY_BATCH_SIZE", 100, int)
MAX_HISTORY_LOAD = get_env("MAX_HISTORY_LOAD", 5000, int)

RESET_ALLOWED_USER_ID = OWNER_USER_ID
SYNC_ALLOWED_USER_ID = OWNER_USER_ID
LOAD_HISTORY_ALLOWED_USER_ID = OWNER_USER_ID
