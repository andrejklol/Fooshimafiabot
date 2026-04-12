from core.cache import app_state
from core.utils import send_error_log
from services.vrchat_client import refresh_vrc_group_members


async def refresh_group_cache_once(force: bool = False) -> None:
    """
    Refresh the VRChat group member cache.

    Safe to call repeatedly.
    Automatically skips if VRChat is not ready yet.
    """

    try:
        if not getattr(app_state, "vrc_groups_api", None):
            return

        await refresh_vrc_group_members(force=force)

    except Exception as exc:
        await send_error_log("Refresh Group Cache Error", exc)
