from .vrchat_auth import login_vrchat

from .vrchat_group import (
    ensure_vrc_group_cache_ready,
    get_all_vrc_staff_members,
    get_cached_vrc_user_role_ids,
    get_cached_vrc_user_roles,
    get_pretty_vrc_name,
    is_cached_vrc_user_staff,
    refresh_group_cache_once,
    refresh_vrc_group_members,
    refresh_vrc_group_roles,
    resolve_vrchat_user_id,
    vrc_user_is_staff,
)

from .vrchat_presence import (
    ensure_pipeline_listener_started,
    get_vrchat_user_status,
    is_vrchat_user_online,
    mark_vrc_user_recently_active,
    stop_pipeline_listener,
)

from .status_pipeline import (
    process_user_status,
    decide_online_with_reason,
)

__all__ = [

    # auth
    "login_vrchat",

    # group
    "ensure_vrc_group_cache_ready",
    "get_all_vrc_staff_members",
    "get_cached_vrc_user_role_ids",
    "get_cached_vrc_user_roles",
    "get_pretty_vrc_name",
    "is_cached_vrc_user_staff",
    "refresh_group_cache_once",
    "refresh_vrc_group_members",
    "refresh_vrc_group_roles",
    "resolve_vrchat_user_id",
    "vrc_user_is_staff",

    # presence
    "ensure_pipeline_listener_started",
    "get_vrchat_user_status",
    "is_vrchat_user_online",
    "mark_vrc_user_recently_active",
    "stop_pipeline_listener",

    # pipeline
    "process_user_status",
    "decide_online_with_reason",
]
