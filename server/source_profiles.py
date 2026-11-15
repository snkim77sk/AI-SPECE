"""Separate source contracts inside the G2B application. No network or task mutations."""
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from pathlib import Path

KST=timezone(timedelta(hours=9))


@dataclass(frozen=True)
class SourceProfile:
    name: str
    credential_env: str
    encrypted_credential: Path
    state_root: Path
    datasets: tuple[str,...]
    allowed_host: str
    maintenance_until: datetime | None=None


PROFILES={
    'g2b':SourceProfile('g2b','G2B_SERVICE_KEY',
        Path('D:/G2B/isolated-prep-20261113/credentials/g2b-current-user.dpapi.json'),
        Path('D:/G2B/live-bounded-20261114'),('shopping_delivery',),'apis.data.go.kr'),
    'lofin':SourceProfile('lofin','LOFIN_API_KEY',
        Path('D:/LOFIN365/secure-setup-20261113/credentials/lofin365-current-user-reentry-20261113.dpapi.json'),
        Path('D:/G2B/lofin-collection'),('budget','budget_appropriation'),'www.lofin365.go.kr',
        datetime(2026,11,15,0,0,tzinfo=KST))
}


def source_readiness(source,clock,*,source_hold=False,transport_verified=False):
    """One source's state never depends on another source's error/hold state."""
    profile=PROFILES[source]
    if clock.tzinfo is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    if source_hold:
        return 'HOLD'
    if profile.maintenance_until and clock.astimezone(KST)<profile.maintenance_until:
        return 'MAINTENANCE'
    return 'READY' if transport_verified else 'FIRST_RESPONSE_VERIFICATION_REQUIRED'


def child_environment(source,parent_env,credential):
    """Call only in the source supervisor; plaintext is never serialized to disk."""
    profile=PROFILES[source]
    env=dict(parent_env)
    for name in ('G2B_SERVICE_KEY','LOFIN_API_KEY'):
        env.pop(name,None)
    if not credential:
        raise ValueError('SOURCE_CREDENTIAL_REQUIRED')
    env[profile.credential_env]=credential
    env['G2B_SOURCE_PROFILE']=source
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env
