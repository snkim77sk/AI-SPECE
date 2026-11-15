// Query defaults only. Collection windows and saved checkpoints never use this.
export function recentQueryDates(now = new Date()) {
  const today = new Date(now.getTime() + 9 * 3600000).toISOString().slice(0, 10);
  const end = new Date(today + 'T00:00:00Z');
  end.setUTCDate(end.getUTCDate() - 1); // These sources publish through yesterday.
  const to = end.toISOString().slice(0, 10);
  end.setUTCDate(end.getUTCDate() - 6);
  return {from: end.toISOString().slice(0, 10), to};
}

export function initialQueryDates(params, now = new Date()) {
  const explicit = ['from', 'to', 'start_date', 'end_date'].some(key => params.has(key));
  return explicit ? {from: params.get('from') ?? params.get('start_date') ?? '', to: params.get('to') ?? params.get('end_date') ?? ''} : recentQueryDates(now);
}
