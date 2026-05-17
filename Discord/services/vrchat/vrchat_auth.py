import asyncio
import base64
import binascii
import logging
import os
import time

import urllib3
from vrchatapi import ApiClient, Configuration
from vrchatapi.api.authentication_api import AuthenticationApi
from vrchatapi.models.two_factor_auth_code import TwoFactorAuthCode
from vrchatapi.models.two_factor_email_code import TwoFactorEmailCode

from core.cache import app_state
from core.config import VRC_CONFIG
from core.utils import (
    format_remaining_cooldown,
    run_blocking,
    send_error_log,
    vrchat_cooldown_active,
)

log = logging.getLogger("vrchat_auth")

VRCHAT_API_RETRIES = 3
VRCHAT_API_BASE_DELAY_SECONDS = 2.0

# ============================================================
# BASIC HELPERS
# ============================================================

def _now_ts() -> float:
    return time.time()

def _ensure_attr_default(attr: str, default) -> None:
    if not hasattr(app_state, attr):
        setattr(app_state, attr, default() if callable(default) else default)

# ============================================================
# APP STATE SETUP
# ============================================================

def _ensure_pipeline_state() -> None:
    _ensure_attr_default("vrc_pipeline_friend_presence", dict)
    _ensure_attr_default("vrc_pipeline_ws", None)
    _ensure_attr_default("vrc_pipeline_task", None)
    _ensure_attr_default("vrc_pipeline_last_event_ts", 0.0)
    _ensure_attr_default("vrc_pipeline_connected", False)

def _ensure_recent_activity_state() -> None:
    _ensure_attr_default("vrc_recent_activity", dict)
    _ensure_attr_default("vrc_friend_presence_cache", dict)
    _ensure_attr_default("vrc_friend_presence_last_refresh", 0.0)

def _ensure_vrc_sync_state() -> None:
    _ensure_attr_default("vrc_group_roles_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_members_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_info_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_roles_refresh_lock", asyncio.Lock)
    _ensure_attr_default("vrc_group_members_refresh_lock", asyncio.Lock)
    _ensure_attr_default("vrc_group_info_refresh_lock", asyncio.Lock)
    _ensure_attr_default("group_cache", dict)
    _ensure_attr_default("vrc_group_info_last_refresh", 0.0)
    _ensure_attr_default("vrc_group_roles_last_refresh", 0.0)
    _ensure_attr_default("vrc_group_members_last_refresh", 0.0)
    _ensure_attr_default("vrc_group_role_map", dict)
    _ensure_attr_default("vrc_group_member_roles", dict)
    _ensure_attr_default("vrc_group_member_role_ids", dict)
    _ensure_attr_default("vrchat_staff_role_ids", set)
    _ensure_attr_default("vrchat_group_roles", dict)
    _ensure_attr_default("vrchat_group_members", dict)
    _ensure_attr_default("target_name_cache", dict)

# ============================================================
# ERROR / RETRY HELPERS
# ============================================================

def _is_connection_reset_error(exc: Exception) -> bool:
    text = str(exc or "")
    return (
        isinstance(exc, ConnectionResetError)
        or any(phrase in text for phrase in [
            "ConnectionResetError", "Connection aborted",
            "forcibly closed", "Max retries exceeded", "ProtocolError"
        ])
    )

def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc or "")
    return "429" in text or "Too Many Requests" in text

def _is_2fa_challenge(exc: Exception) -> bool:
    text = str(exc or "")
    return any(s in text for s in [
        "requiresTwoFactorAuth", "Email 2 Factor", "2 Factor Authentication",
        "emailOtp", "totp",
    ])

async def _run_vrc_api_call(func, *args, retries: int = VRCHAT_API_RETRIES, **kwargs):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await run_blocking(func, *args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= retries: raise
            if _is_rate_limit_error(exc): raise
            if _is_2fa_challenge(exc): raise
            delay = VRCHAT_API_BASE_DELAY_SECONDS * attempt
            log.warning(f"VRChat API retry {attempt}/{retries} | {type(exc).__name__}: {exc.status if hasattr(exc, 'status') else ''}")
            await asyncio.sleep(delay)
    raise last_exc

async def _send_rate_limited_error(title: str, exc: Exception, attr_name: str, cooldown: int = 300) -> None:
    _ensure_vrc_sync_state()
    now = _now_ts()
    last_sent = float(getattr(app_state, attr_name, 0.0))
    if (now - last_sent) < cooldown and _is_connection_reset_error(exc): return
    setattr(app_state, attr_name, now)
    await send_error_log(title, f"{type(exc).__name__}: {exc}")

# ============================================================
# LOGIN
# ============================================================

def _finalise_login(api_client: ApiClient, auth_api: AuthenticationApi) -> None:
    from vrchatapi.api.groups_api import GroupsApi
    from vrchatapi.api.users_api import UsersApi
    app_state.vrc_client = api_client
    app_state.vrc_auth_api = auth_api
    app_state.vrc_groups_api = GroupsApi(api_client)
    app_state.vrc_users_api = UsersApi(api_client)
    _ensure_pipeline_state()
    _ensure_recent_activity_state()
    _ensure_vrc_sync_state()
    from .vrchat_presence import ensure_pipeline_listener_started
    ensure_pipeline_listener_started()

def _needs_2fa(err: str) -> bool:
    return any(s in err for s in [
        "requiresTwoFactorAuth", "Email 2 Factor", "emailOtp", "totp",
        "Two-factor", "two factor", "2 Factor",
    ])

def _2fa_is_email(err: str) -> bool:
    return "emailOtp" in err or "Email 2 Factor" in err

def _validate_totp_secret(secret: str) -> bool:
    try:
        base64.b32decode(secret, casefold=True)
        return True
    except (binascii.Error, ValueError):
        return False

async def login_vrchat() -> bool:
    from .vrchat_client import _USER_AGENT

    # Try saved cookie — single attempt, no retries (expired = expired)
    auth_cookie = os.getenv("VRCHAT_AUTH_COOKIE", "").strip()
    if auth_cookie:
        log.info("Attempting VRChat login using saved cookie...")
        try:
            config = Configuration()
            config.retries = urllib3.Retry(total=0)
            api_client = ApiClient(config)
            api_client.user_agent = _USER_AGENT
            api_client.default_headers["Cookie"] = f"auth={auth_cookie}"
            auth_api = AuthenticationApi(api_client)
            user = await run_blocking(auth_api.get_current_user)
            _finalise_login(api_client, auth_api)
            from .vrchat_presence import _refresh_friend_presence_cache
            await _refresh_friend_presence_cache(force=True)
            log.info(f"Cookie login success: {user.display_name}")
            return True
        except Exception as exc:
            log.info(f"Saved cookie expired, falling back to credentials")

    # Fallback to username/password
    creds = (VRC_CONFIG['username'], VRC_CONFIG['password'])
    if not all(creds):
        log.error("VRChat credentials missing in VRC_CONFIG")
        return False

    if vrchat_cooldown_active():
        log.warning(f"Login skipped. Cooldown: {format_remaining_cooldown()}")
        return False

    log.info("Attempting VRChat login via username/password...")
    config = Configuration(username=creds[0], password=creds[1])
    config.retries = urllib3.Retry(total=0)
    api_client = ApiClient(config)
    api_client.user_agent = _USER_AGENT
    auth_api = AuthenticationApi(api_client)

    async def _complete_login(user_obj) -> bool:
        _finalise_login(api_client, auth_api)
        from .vrchat_group import refresh_group_cache_once
        from .vrchat_presence import _refresh_friend_presence_cache
        await _refresh_friend_presence_cache(force=True)
        await refresh_group_cache_once(force=True)
        return True

    err = ""
    try:
        user = await run_blocking(auth_api.get_current_user)
        return await _complete_login(user)
    except Exception as exc:
        err = str(exc)
        if _is_rate_limit_error(exc):
            await send_error_log("VRChat Login Rate Limited", "Too many failed attempts. Wait 1 hour before restarting.")
            return False
        if not _needs_2fa(err):
            await send_error_log("VRChat Login Failed", err)
            return False

    # Route based on which 2FA type VRChat is asking for
    if _2fa_is_email(err):
        totp_secret = VRC_CONFIG.get('totp_secret', '').strip()
        if totp_secret:
            log.warning("VRChat is asking for Email OTP but VRCHAT_TOTP_SECRET is set. Switch account 2FA to Authenticator App.")
        otp = VRC_CONFIG.get('otp', '').strip()
        if not otp:
            log.error("Email OTP required but VRCHAT_EMAIL_OTP is not set in environment")
            return False
        log.info("Submitting email 2FA code...")
        try:
            await _run_vrc_api_call(auth_api.verify2_fa_email_code, TwoFactorEmailCode(code=otp))
            user = await run_blocking(auth_api.get_current_user)
            return await _complete_login(user)
        except Exception as otp_exc:
            await send_error_log("VRChat Email 2FA Failed", str(otp_exc))
            return False

    # TOTP (authenticator app)
    totp_secret = VRC_CONFIG.get('totp_secret', '').strip()
    if not totp_secret:
        log.error("TOTP 2FA required but VRCHAT_TOTP_SECRET is not set in environment")
        return False

    totp_secret = totp_secret.upper().replace(" ", "").replace("-", "")
    if not _validate_totp_secret(totp_secret):
        log.error("VRCHAT_TOTP_SECRET is not a valid base32 key. Copy the text secret key from VRChat's authenticator setup page.")
        return False

    try:
        import pyotp
    except ImportError:
        log.error("pyotp not installed — run: pip install pyotp")
        return False

    code = pyotp.TOTP(totp_secret).now()
    log.info("Submitting TOTP 2FA code...")
    try:
        await _run_vrc_api_call(auth_api.verify2_fa, TwoFactorAuthCode(code=code))
        user = await run_blocking(auth_api.get_current_user)
        return await _complete_login(user)
    except Exception as totp_exc:
        await send_error_log("VRChat TOTP 2FA Failed", str(totp_exc))
        return False

__all__ = [
    "login_vrchat", "_ensure_pipeline_state", "_ensure_recent_activity_state",
    "_ensure_vrc_sync_state", "_finalise_login", "_is_connection_reset_error",
    "_run_vrc_api_call", "_send_rate_limited_error",
]
