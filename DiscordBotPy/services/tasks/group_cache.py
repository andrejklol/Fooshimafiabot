from core.cache import app_state
from core.utils import send_error_log

from services.vrchat_client import refresh_vrc_group_members


async def refresh_group_cache_once() -> None:
    """
    Refresh VRChat group member cache.

    Safe to call repeatedly.
    Will skip automatically if:
    • VRChat not logged in yet
    • API not ready
    """

    try:

        # VRChat not ready yet
        if not getattr(app_state, "vrc_groups_api", None):
            return

        await refresh_vrc_group_members(force=False)

    except Exception as exc:

        await send_error_log(
            "Refresh Group Cache Error",
            exc,
        )