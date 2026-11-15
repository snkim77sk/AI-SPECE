/** Pure, offline QWGJK history planner. All dates use KST calendar-day strings.
 * now is optional injected epoch milliseconds/Date for lease/cooldown decisions.
 * No network, storage, environment, or mutation of caller input.
 */
const DAY = 86400000;
const START = '2026-01-01';
const ACTIVE = new Set(['ready', 'running']);
function parseDay(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw Error('INVALID_DAY');
  const epoch = Date.parse(value + 'T00:00:00Z');
  if (!Number.isFinite(epoch) || new Date(epoch).toISOString().slice(0, 10) !== value) throw Error('INVALID_DAY');
  return epoch;
}
const isoDay = epoch => new Date(epoch).toISOString().slice(0, 10);
function kstDay(epoch) { return isoDay(epoch + 9 * 3600000); }
function positiveInteger(value, label, allowZero = true) {
  if (!Number.isSafeInteger(value) || value < (allowZero ? 0 : 1)) throw Error(label);
  return value;
}
export function budgetHistoryWindow(today, retentionDays = 365) {
  const epoch = parseDay(today);
  positiveInteger(retentionDays, 'INVALID_RETENTION_DAYS', false);
  const days = Math.min(retentionDays, 365);
  const calculated = isoDay(epoch - days * DAY);
  const from = calculated > START ? calculated : START;
  const to = isoDay(epoch - DAY);
  return { from, to, retentionDays: days, empty: from > to };
}
/** Number of remaining permits reserved for history; always leaves one current permit. */
export function historyReserve(remainingQuota, cap = 20) {
  positiveInteger(remainingQuota, 'INVALID_REMAINING_QUOTA');
  positiveInteger(cap, 'INVALID_HISTORY_RESERVE');
  if (remainingQuota <= 1 || cap === 0) return 0;
  return Math.min(cap, Math.max(1, Math.floor(remainingQuota / 4)), remainingQuota - 1);
}
/** Strings mean already-verified nationwide COMPLETE dates. Object entries are
 * validated ledger rows: Sites sessions or original scope_key checkpoints.
 * Region partitions, synthetic sessions, malformed entries and partial statuses
 * do not qualify. Both current and history QWGJK completion count.
 */
export function completedHistoryDates(entries = []) {
  const dates = new Set();
  for (const entry of entries) {
    if (typeof entry === 'string') { parseDay(entry); dates.add(entry); continue; }
    if (!entry || typeof entry !== 'object') continue;
    if (String(entry.state ?? entry.status ?? '').toLowerCase() !== 'complete') continue;
    if (entry.mode && entry.mode !== 'live') continue;
    if (entry.service && entry.service !== 'QWGJK') continue;
    if (entry.region_code || entry.region || entry.partitioned || entry.nationwide === false) continue;
    let date, year;
    if (entry.scope_key !== undefined) {
      const match = /^(?:history:)?(\d{4}):(\d{4}-\d{2}-\d{2})$/.exec(entry.scope_key);
      if (!match) continue;
      [, year, date] = match;
    } else {
      date = entry.snapshot_date;
      year = entry.fiscal_year;
    }
    try { parseDay(date); } catch { continue; }
    if (year !== undefined && String(year) !== date.slice(0, 4)) continue;
    dates.add(date);
  }
  return dates;
}
function timestamp(value) {
  if (value instanceof Date) return value.getTime();
  if (typeof value === 'number') return value;
  // Require a timezone. Reject ambiguous timestamps rather than depend on host TZ.
  if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) return NaN;
  return Date.parse(value);
}
function decision(action, reason, extras = {}) {
  return { action, reason, service: 'QWGJK', historyOnly: true, maximumPages: 1, ...extras };
}
/** pendingHistory is one authoritative selected unfinished history session.
 * Caller must not hide older paused/cancelled plans or pass only latest eight rows.
 * userControl is 'active'|'paused'|'cancelled' (or {state: ...}).
 * currentState may be null when no current session exists. To preserve current
 * priority, no history starts in that case. now must be on supplied KST today.
 */
/** @param {any} input */
export function budgetHistoryDecision({
  today, completedDates = [], pendingHistory = null, remainingQuota,
  currentState = null, userControl = 'active', now = new Date(), retentionDays = 365, mode = 'live',
}) {
  if (!['live', 'synthetic'].includes(mode)) throw Error('INVALID_MODE');
  const window = budgetHistoryWindow(today, retentionDays);
  positiveInteger(remainingQuota, 'INVALID_REMAINING_QUOTA');
  const nowMs = timestamp(now);
  if (!Number.isFinite(nowMs) || kstDay(nowMs) !== today) throw Error('NOW_TODAY_MISMATCH');
  const control = typeof userControl === 'string' ? userControl : userControl?.state;
  if (control === 'paused' || control === 'cancelled') return decision('stop', control === 'paused' ? 'USER_PAUSED' : 'USER_CANCELLED');
  if (control !== 'active') return decision('stop', 'INVALID_USER_CONTROL');
  // Sticky operator controls take precedence over quota/current checks.
  if (pendingHistory?.state === 'paused' || pendingHistory?.state === 'cancelled') {
    return decision('stop', pendingHistory.state === 'paused' ? 'USER_PAUSED' : 'USER_CANCELLED', { id: pendingHistory.id });
  }
  if (window.empty) return decision('wait', 'HISTORY_UP_TO_DATE', { window });
  if (!currentState) return decision('wait', 'WAITING_CURRENT');
  if ((currentState.service && currentState.service !== 'QWGJK') || (currentState.mode && currentState.mode !== mode) || currentState.history_only) return decision('stop', 'INVALID_CURRENT_SESSION');
  if (!Number.isFinite(Number(currentState.lease_until || 0))) return decision('stop', 'INVALID_CURRENT_LEASE');
  if (currentState.state === 'paused' || currentState.state === 'cancelled') {
    return decision('stop', currentState.state === 'paused' ? 'CURRENT_USER_PAUSED' : 'CURRENT_USER_CANCELLED');
  }
  if (currentState.state === 'error') return decision('stop', 'CURRENT_SOURCE_ERROR', { error: currentState.error || 'SESSION_ERROR' });
  if (!['complete', ...ACTIVE].includes(currentState.state)) return decision('stop', 'INVALID_CURRENT_STATE');
  if (Number(currentState.lease_until || 0) > nowMs) return decision('wait', 'CURRENT_SESSION_BUSY');
  const available = currentState.state === 'complete' ? remainingQuota : historyReserve(remainingQuota);
  if (available <= 0) return decision('wait', remainingQuota === 0 ? 'LOFIN_LOCAL_DAILY_BUDGET_REACHED' : 'CURRENT_QUOTA_RESERVED');
  let snapshot;
  if (pendingHistory) {
    const s = pendingHistory;
    if (s.service && s.service !== 'QWGJK' || ![true, 1].includes(s.history_only)) return decision('stop', 'INVALID_HISTORY_SESSION');
    if (s.mode && s.mode !== mode) return decision('stop', 'INVALID_HISTORY_SESSION');
    try { parseDay(s.snapshot_date); } catch { return decision('stop', 'INVALID_HISTORY_SESSION'); }
    if (s.snapshot_date < window.from || s.snapshot_date > window.to) return decision('stop', 'OUTSIDE_HISTORY_WINDOW', { id: s.id, snapshot: s.snapshot_date });
    if (!s.id || (s.fiscal_year !== undefined && String(s.fiscal_year) !== s.snapshot_date.slice(0,4))) return decision('stop', 'INVALID_HISTORY_SESSION');
    if (s.state === 'complete') return decision('stop', 'COMPLETED_SESSION_PASSED_AS_PENDING', { id: s.id });
    if (!['error', ...ACTIVE].includes(s.state)) return decision('stop', 'INVALID_HISTORY_STATE', { id: s.id });
    if (!Number.isFinite(Number(s.lease_until || 0))) return decision('stop', 'INVALID_HISTORY_LEASE', { id: s.id });
    if (Number(s.lease_until || 0) > nowMs) return decision('wait', 'SESSION_BUSY', { id: s.id });
    if (s.state === 'error') {
      const updated = timestamp(s.updated_at);
      if (!Number.isFinite(updated) || updated > nowMs) return decision('stop', 'INVALID_ERROR_TIMESTAMP', { id: s.id });
      if (s.error === 'REPEATED_OR_OVERLAPPING_PAGE' && s.replay_count === 0) return decision('replay', 'CONFIRMED_OVERLAP_REPLAY_ONCE', {id:s.id,snapshot:s.snapshot_date,year:s.fiscal_year,page:1});
      if (s.error === 'LOFIN_LOCAL_DAILY_BUDGET_REACHED') {
        if (kstDay(updated) >= today) return decision('wait', 'LOFIN_LOCAL_DAILY_BUDGET_REACHED', { id: s.id });
      } else if (s.error === 'SOURCE_NETWORK_OR_TIMEOUT') {
        if (nowMs - updated < 900000) return decision('wait', 'TRANSIENT_RETRY_COOLDOWN', { id: s.id });
      } else return decision('stop', s.error || 'SESSION_ERROR', { id: s.id });
    }
    if (!Number.isSafeInteger(s.page) || s.page < 1) return decision('stop', 'INVALID_HISTORY_PAGE', { id: s.id });
    return decision('step', 'RESUME_HISTORY_PAGE', { id: s.id, snapshot: s.snapshot_date, year: Number(s.snapshot_date.slice(0,4)), page: s.page, reservedRequests: available });
  }
  const completed = completedHistoryDates(completedDates);
  for (let epoch = parseDay(window.from); epoch <= parseDay(window.to); epoch += DAY) {
    const candidate = isoDay(epoch);
    if (!completed.has(candidate)) { snapshot = candidate; break; }
  }
  if (!snapshot) return decision('wait', 'HISTORY_UP_TO_DATE', { window });
  return decision('create', 'OLDEST_UNFINISHED_DAY', { snapshot, year: Number(snapshot.slice(0,4)), page: 1, reservedRequests: available, window });
}
