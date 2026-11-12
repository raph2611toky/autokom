"""Pure, deterministic AUTOKOM launch-offer policy (no I/O, no Django)."""
from datetime import timedelta

DAYS = {(False, False): 30, (False, True): 15, (True, False): 15, (True, True): 7}
GRACE = timedelta(hours=24)


def entitlement_window(first_publication, *, has_backend=False, high_availability=False,
                       unlimited=False, now=None):
    """First successful publication anchors time forever; feature upgrades never renew it."""
    if unlimited:
        return {'state': 'unlimited', 'days': None, 'expires_at': None,
                'purge_after': None, 'remaining_seconds': None}
    days = DAYS[(bool(has_backend), bool(high_availability))]
    if first_publication is None:
        return {'state': 'not_started', 'days': days, 'expires_at': None,
                'purge_after': None, 'remaining_seconds': None}
    expires = first_publication + timedelta(days=days)
    purge_after = expires + GRACE
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
    remaining = max(0, int((expires - now).total_seconds()))
    state = ('active' if now < expires else
             'grace' if now < purge_after else 'purge_due')
    return {'state': state, 'days': days, 'expires_at': expires,
            'purge_after': purge_after, 'remaining_seconds': remaining}
