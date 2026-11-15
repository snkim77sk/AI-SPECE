"""Windows collection policy authorized by the user's automatic-collection request."""
from datetime import datetime,timezone,timedelta

KST=timezone(timedelta(hours=9))
INITIAL_DAY='2026-11-14'
INITIAL_AVAILABLE=900
DAILY_CEILING=950
# Leave a 30-second gap after the existing 90-second outer bound. A slow
# two-page cycle can still use the daily budget while healthy history remains.
INTERVAL_MINUTES=2


def day_policy(clock):
    if clock.tzinfo is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    day=clock.astimezone(KST).date().isoformat()
    if day<INITIAL_DAY:
        raise ValueError('CLOCK_BEFORE_AUTHORIZATION')
    return {'budget_day':day,'budget_basis':'USER_INITIAL_ESTIMATE_AND_KST_RESET',
            'initial_assumption_day':INITIAL_DAY,'initial_assumed_available':INITIAL_AVAILABLE,
            'configured_daily_ceiling':DAILY_CEILING,
            'effective_day_budget':INITIAL_AVAILABLE if day==INITIAL_DAY else DAILY_CEILING,
            'provider_remaining_verified':False,'quota_reset_at_kst':(clock.astimezone(KST)+timedelta(days=1)).replace(
                hour=0,minute=0,second=0,microsecond=0).isoformat(timespec='seconds')}


def slot_id(clock):
    local=clock.astimezone(KST)
    slot=local.replace(minute=local.minute//INTERVAL_MINUTES*INTERVAL_MINUTES,second=0,microsecond=0)
    return 'scheduled-'+slot.strftime('%Y%m%dT%H%M')


def cycle_fits_before_reset(clock,seconds=90):
    reset=datetime.fromisoformat(day_policy(clock)['quota_reset_at_kst'])
    return (reset-clock.astimezone(KST)).total_seconds()>seconds


def verify_plan_budget(plan,clock):
    expected=day_policy(clock)
    for key in ('budget_day','budget_basis','effective_day_budget','configured_daily_ceiling',
                'initial_assumption_day','initial_assumed_available'):
        if plan.get(key)!=expected[key]:
            raise ValueError('SCHEDULE_BUDGET_MISMATCH')
    if plan.get('schedule_name')!='G2B-Bounded-Backfill-20260101':
        raise ValueError('UNRECOGNIZED_SCHEDULE')
    return expected['effective_day_budget']
