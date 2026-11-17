const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const linkId = require.resolve('next/link');
require.cache[linkId] = { id: linkId, filename: linkId, loaded: true, exports: { __esModule: true,
  default: ({ children, ...props }) => React.createElement('a', props, children) } };
const Summary = require('../components/checkin/CheckinResultSummary.tsx').default;
const AvailableSessions = require('../components/student/AvailableSessions.tsx').default;
const History = require('../components/student/AttendanceHistory.tsx').default;
const Courses = require('../components/student/EnrolledCourses.tsx').default;
const { StatusBadge } = require('../components/ui/Feedback.tsx');
const { riskSignalCodes, riskSignalExplanation } = require('../lib/verification-messages.ts');
const { formatJoinedDate, formatSessionRange } = require('../lib/student-display.ts');
const ModalFrame = require('../components/ui/ModalFrame.tsx').default;
let renderer;
const render = async element => { await act(async () => { renderer = create(element); }); return JSON.stringify(renderer.toJSON()); };
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); renderer = undefined; });
const result = { id: 'c', session_id: 's', student_id: 'u', checked_in_at: '2026-11-05T00:00:00',
  status: 'rejected', risk_score: .4289, liveness_passed: true, liveness_score: .98,
  face_match_passed: true, face_match_score: .89, qr_code_verified: false, distance_from_venue_meters: 1300.56,
  risk_factors: '["geo_out_of_bounds","attestation_failed","vpn_detected"]' };

test('modal includes privacy disclosures in the focus trap, respects busy Escape and restores focus', async () => {
  const originalDocument = global.document, originalElement = global.HTMLElement;
  let handler, selector, closed = 0;
  class Element {
    constructor() { this.isConnected = true; }
    focus() { document.activeElement = this; }
    getClientRects() { return [{}]; }
  }
  const previous = new Element(), summary = new Element(), last = new Element(), dialog = new Element();
  dialog.querySelectorAll = value => { selector = value; return [summary,last]; };
  dialog.addEventListener = (name, callback) => { if (name === 'keydown') handler = callback; };
  dialog.removeEventListener = () => {};
  global.HTMLElement = Element;
  global.document = { activeElement:previous, body:{style:{overflow:'auto'}} };
  try {
    const contents = busy => React.createElement(ModalFrame,{label:'Check-in',busy,panelClassName:'checkin-dialog',onClose(){closed++;}},
      React.createElement('details',null,React.createElement('summary',null,'Privacy & evidence')),
      React.createElement('button',null,'Cancel'));
    await act(async () => {renderer=create(contents(false),{createNodeMock: node=>node.props.role==='dialog'?dialog:null});});
    assert.equal(document.activeElement,dialog); assert.equal(document.body.style.overflow,'hidden');
    last.focus(); handler({key:'Tab',preventDefault(){}}); assert.equal(document.activeElement,summary);
    assert.match(selector,/summary/);
    summary.focus(); handler({key:'Tab',shiftKey:true,preventDefault(){}}); assert.equal(document.activeElement,last);
    await act(async () => renderer.update(contents(true)));
    handler({key:'Escape',preventDefault(){}}); assert.equal(closed,0);
    await act(async () => renderer.update(contents(false)));
    handler({key:'Escape',preventDefault(){}}); assert.equal(closed,1);
    await act(async () => renderer.unmount()); renderer=undefined;
    assert.equal(document.body.style.overflow,'auto'); assert.equal(document.activeElement,previous);
  } finally {
    if(renderer) {await act(async()=>renderer.unmount());renderer=undefined;}
    global.document=originalDocument; global.HTMLElement=originalElement;
  }
});

test('location rejection explains recorded signals despite high biometric scores and low overall risk', async () => {
  const output = await render(React.createElement(Summary, { result, activeChallenge: true }));
  assert.match(output, /outside the permitted attendance area/); assert.match(output, /1301 m/);
  assert.match(output, /0.429/); assert.match(output, /0.98/); assert.match(output, /0.89/);
  assert.match(output, /not conclusive proof of VPN use/); assert.match(output, /attestation was not established/);
  assert.match(output, /Lower aggregate risk does not override/);
});
test('failed active challenge explains effective zero without presenting image quality or a real-person probability', async () => {
  const output = await render(React.createElement(Summary, { result: { ...result, liveness_passed: false, liveness_score: 0,
    risk_factors: '["liveness_failed"]' }, activeChallenge: true }));
  assert.match(output, /effective score of 0.00 on challenge failure/);
  assert.match(output, /not an image-quality score or a probability/);
  assert.match(output, /not completed or verified/); assert.doesNotMatch(output, /0%/);
});
test('unavailable liveness evidence is not shown as a measured zero', async () => {
  const output = await render(React.createElement(Summary, { result: { ...result, liveness_passed: null, liveness_score: null }, activeChallenge: true }));
  assert.match(output, /Not verified/); assert.doesNotMatch(output, /effective score 0.00|on challenge failure/);
});
for (const status of ['approved','pending','flagged','rejected','appealed']) {
  test(`history retains the recorded ${status} status`, async () => {
    const output = await render(React.createElement(History, { items: [{ ...result, status }], total: 1, complete: true,
      loading: false, error: '', labels: { s: 'Location lecture' }, onRefresh(){}, onLoadMore(){} }));
    assert.match(output, new RegExp(`tag-${status}`)); assert.match(output, /Location lecture/);
    assert.match(output, /Recent Attendance/);
    assert.doesNotMatch(output, /Contact course staff|awaiting staff review/);
  });
}
for (const status of ['approved','rejected']) {
  test(`recent ${status} badge is text only while badges elsewhere retain their icons`, async () => {
    await render(React.createElement(History, { items: [{ ...result, status }], total: 1, complete: true,
      loading: false, error: '', labels: { s: 'Attendance session' }, onRefresh(){}, onLoadMore(){} }));
    const badge = renderer.root.findAllByType('span').find(node => node.props.className === `tag tag-${status}`);
    assert.ok(badge); assert.equal(badge.findAllByType('svg').length, 0);
    assert.equal(badge.findByProps({ className:'capitalize' }).children.join(''), status);
    await act(async () => renderer.update(React.createElement(StatusBadge, { status })));
    assert.equal(renderer.root.findAllByType('svg').length, 1);
  });
}
test('signal parsing safely handles missing, malformed and unknown values without guessing', () => {
  for (const raw of [null, undefined, '', 'bad-json', '{}', '[null,42,"<script>"]']) assert.deepEqual(riskSignalCodes(raw), []);
  assert.deepEqual(riskSignalCodes('["geo_out_of_bounds","geo_out_of_bounds"]'), ['geo_out_of_bounds']);
  assert.match(riskSignalExplanation('future_signal'), /additional verification signal/);
});
const course = { id:'course', code:'SC3099', name:'Capstone', require_face_recognition:true, require_device_binding:true };
const session = { id:'s', course_id:'course', name:'Lecture', status:'active', session_type:'lecture', venue_name:'Seminar room',
  scheduled_start:'2026-11-05T00:00:00', scheduled_end:'2026-11-05T02:00:00',
  checkin_opens_at:'2020-01-01T00:00:00', checkin_closes_at:'2099-01-01T00:00:00', require_face_match:true, require_liveness_check:true };
const props = { sessions:[session], courses:[course], attempts:new Map(), loading:false, historyReady:true, error:'',
  user:{ camera_consent:true, geolocation_consent:true, face_enrolled:true }, onRefresh(){}, findExistingAttempt:async()=>null, onBegin(){} };
test('session card displays context and an available action using the existing policy', async () => {
  const output = await render(React.createElement(AvailableSessions, props));
  assert.match(output, /SC3099/); assert.match(output, /Seminar room/); assert.match(output, /Check-in open/);
  assert.equal(renderer.root.findAllByType('button').find(n => n.children.join('') === 'Check In').props.disabled, false);
});
test('settled session is described as recorded, never inferred to be approved', async () => {
  const output = await render(React.createElement(AvailableSessions, { ...props, attempts:new Map([['s','settled']]) }));
  assert.match(output, /Submission recorded/);
  assert.equal(renderer.root.findAllByType('button').find(n => n.children.join('') === 'Already Checked In').props.disabled, true);
});
test('missing setup remains blocked with a profile action', async () => {
  const output = await render(React.createElement(AvailableSessions, { ...props, user:{ ...props.user, camera_consent:false } }));
  assert.match(output, /Setup needed/); assert.ok(renderer.root.findAllByProps({href:'/profile'}).length);
  assert.equal(renderer.root.findAllByType('button').find(n => n.children.join('') === 'Check In').props.disabled, true);
});
test('course empty state does not invent enrollment or unsupported actions', async () => {
  const output = await render(React.createElement(Courses, { courses:[], loading:false, error:'' }));
  assert.match(output, /No enrolled courses were returned/); assert.equal(renderer.root.findAllByType('button').length, 0);
});

test('approved result with recorded signals never claims all checks passed', async () => {
  const output = await render(React.createElement(Summary, { result: { ...result, status: 'approved', face_match_passed: false }, activeChallenge: true }));
  assert.match(output, /Check-in Approved/); assert.match(output, /Not passed/);
  assert.match(output, /Recorded verification signals/); assert.match(output, /attestation was not established/);
  assert.doesNotMatch(output, /All verification checks passed/);
});
test('session card uses the actual recorded status independently from retry eligibility', async () => {
  const output = await render(React.createElement(AvailableSessions, { ...props, attempts: new Map([['s','settled']]), records: [{ ...result, status: 'approved' }] }));
  assert.match(output, /tag-approved/); assert.match(output, /approved/);
  assert.equal(renderer.root.findAllByType('button').find(n => n.children.join('') === 'Already Checked In').props.disabled, true);
});
test('refresh actions retain accessible names and invoke their original callbacks', async () => {
  let refreshed = 0;
  await render(React.createElement(AvailableSessions, { ...props, onRefresh(){ refreshed++; } }));
  await act(async () => renderer.root.findByProps({ 'aria-label': 'Refresh sessions' }).props.onClick());
  assert.equal(refreshed, 1);
});
test('compact dates preserve UTC parsing and Singapore display without changing time policy', () => {
  assert.equal(formatJoinedDate('2026-11-04T23:30:00'), '5 Nov 2026');
  assert.equal(formatJoinedDate(undefined), 'Date unavailable');
  assert.equal(formatSessionRange('invalid', 'invalid'), 'Time unavailable');
  assert.match(formatSessionRange('2026-11-05T00:00:00Z','2026-11-05T02:00:00Z'), /5 Nov 2026/);
});
