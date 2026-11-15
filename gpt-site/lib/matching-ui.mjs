/** UI-only selection tokens. No storage or provider operations. */
export function matchingMode(search) {
  return 'live';
}
/** @param {any} job @param {string} mode @param {string|null|undefined} id */
export function matchingJobMatches(job, mode, id = null) {
  return !!job && job.mode === mode && (!id || job.id === id);
}
export function createMatchingReadGuard() {
  let epoch = 0;
  const versions = new Map();
  return {
    invalidate() { epoch++; versions.clear(); },
    begin(channel) {
      const version = (versions.get(channel) || 0) + 1;
      versions.set(channel, version);
      return {epoch, channel, version};
    },
    current(token) { return token.epoch === epoch && versions.get(token.channel) === token.version; },
  };
}
