const identifier = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const existingBrowserFixture = (userId, changes = {}) => ({
  id: 'registered-browser', user_id: userId, device_fingerprint: identifier,
  platform: 'web', is_active: true, is_trusted: false, trust_score: 'low', total_checkins: 0,
  first_seen_at: '2026-10-25T00:00:00', last_seen_at: '2026-10-25T00:00:00', ...changes,
});
module.exports = { identifier, existingBrowserFixture };
