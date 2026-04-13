import asyncio
from collections import Counter, defaultdict


class AppState:
    def __init__(self):
        self.bot = None
        self.startup_complete = False

        # ============================================================
        # VRCHAT API CLIENTS
        # ============================================================
        self.vrc_client = None
        self.vrc_auth_api = None
        self.vrc_groups_api = None
        self.vrc_users_api = None

        # ============================================================
        # TIMESTAMPS / STATUS
        # ============================================================
        self.startup_timestamp = None
        self.last_log_received_at = None

        self.api_banned_until = None
        self.api_retry_after = None
        self.last_api_error = None

        # ============================================================
        # LOCKS / DIRTY FLAGS
        # ============================================================
        self.leaderboard_lock = asyncio.Lock()
        self.leaderboard_dirty = False

        # ============================================================
        # ARCHIVE GRACE PERIOD TRACKING
        # staff_id -> {"missing_since": iso_timestamp}
        # ============================================================
        self.archive_pending = {}

        # prevents duplicate 12h warning spam
        self.archive_warning_sent = {}

        # ============================================================
        # OVERALL LEADERBOARD COUNTS
        # ============================================================
        self.kick_counts = Counter()
        self.ban_counts = Counter()
        self.warn_counts = Counter()
        self.invite_counts = Counter()

        # ============================================================
        # MONTHLY LEADERBOARD COUNTS
        # Resets automatically on the 1st of each month
        # ============================================================
        self.monthly_kick_counts = Counter()
        self.monthly_ban_counts = Counter()
        self.monthly_warn_counts = Counter()
        self.monthly_invite_counts = Counter()
        self.monthly_reset_key = None

        # ============================================================
        # PROCESSED LOG TRACKING
        # ============================================================
        self.processed_log_ids = set()

        # ============================================================
        # REPEAT OFFENDER TRACKING
        # ============================================================
        self.repeat_offender_actions = defaultdict(list)
        self.repeat_alerted_keys = set()

        # ============================================================
        # PENDING INVITE TRACKING
        # target_id -> invite metadata
        # ============================================================
        self.pending_invites_by_target = {}

        # ============================================================
        # VRCHAT ROLE / MEMBER / NAME CACHE
        # ============================================================
        self.target_name_cache = {}

        # Primary canonical caches
        self.vrchat_group_roles = {}
        self.vrchat_group_members = {}
        self.vrchat_staff_role_ids = set()

        # Member role id cache
        self.vrc_group_member_role_ids = {}

        # Group info cache
        self.group_cache = {}

        # Legacy / compatibility aliases used by different files
        self.group_roles = self.vrchat_group_roles
        self.group_members = self.vrchat_group_members
        self.group_member_cache = self.vrchat_group_members

        self.vrc_group_role_map = self.vrchat_group_roles
        self.vrc_group_member_roles = self.vrchat_group_members

        self.staff_role_ids = self.vrchat_staff_role_ids
        self.vrc_staff_role_ids = self.vrchat_staff_role_ids
        self.cached_staff_role_ids = self.vrchat_staff_role_ids

        self.vrc_group_roles_last_refresh = 0.0
        self.vrc_group_members_last_refresh = 0.0
        self.vrc_group_info_last_refresh = 0.0

        self.vrc_group_roles_last_error_ts = 0.0
        self.vrc_group_members_last_error_ts = 0.0
        self.vrc_group_info_last_error_ts = 0.0

        self.vrc_group_roles_refresh_lock = asyncio.Lock()
        self.vrc_group_members_refresh_lock = asyncio.Lock()
        self.vrc_group_info_refresh_lock = asyncio.Lock()

        # ============================================================
        # STATUS / ONLINE CACHE
        # Used by status pipeline to track previous online state
        # ============================================================
        self.user_online_cache = {}
        self.online_status_cache = self.user_online_cache

        # Optional helper caches for status / presence systems
        self.friend_presence_cache = {}
        self.last_status_signal = {}
        self.last_mod_action_by_user = {}

        # ============================================================
        # VRCHAT PIPELINE STATE
        # ============================================================
        self.vrc_pipeline_friend_presence = {}
        self.vrc_pipeline_ws = None
        self.vrc_pipeline_task = None
        self.vrc_pipeline_last_event_ts = 0.0
        self.vrc_pipeline_connected = False

        # ============================================================
        # RECENT ACTIVITY / FRIEND PRESENCE CACHES
        # ============================================================
        self.vrc_recent_activity = {}
        self.vrc_friend_presence_cache = {}
        self.vrc_friend_presence_last_refresh = 0.0

    def sync_cache_aliases(self) -> None:
        """
        Re-point compatibility aliases to the current canonical objects.
        Call this after replacing a whole cache object, e.g.:
            app_state.vrchat_group_members = new_members
            app_state.sync_cache_aliases()
        """
        self.group_roles = self.vrchat_group_roles
        self.group_members = self.vrchat_group_members
        self.group_member_cache = self.vrchat_group_members

        self.vrc_group_role_map = self.vrchat_group_roles
        self.vrc_group_member_roles = self.vrchat_group_members

        self.staff_role_ids = self.vrchat_staff_role_ids
        self.vrc_staff_role_ids = self.vrchat_staff_role_ids
        self.cached_staff_role_ids = self.vrchat_staff_role_ids

        self.online_status_cache = self.user_online_cache


app_state = AppState()
