// Evaluator-only scheduling checks. Never copy this file into the reviewed checkout.
// Executes pinned controller functions with deferred writes; no npm install or MongoDB.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const vm = require('node:vm');
const repo = process.argv[2];
if (!repo) throw new Error('usage: node evals/verify_smartgc.cjs /path/to/local/clone');
const path = 'src/pickup/trip/trip.controller.js';
function load(sha, mocks) {
  const original = execFileSync('git', ['-C', repo, 'show', `${sha}:${path}`], {encoding: 'utf8'});
  const source = original.replace(/^import .*;\s*$/gm, '').replace(/export const /g, 'var ');
  const context = vm.createContext(mocks);
  vm.runInContext(source, context, {timeout: 1000});
  return context;
}
function response() {
  return {sent: false, code: 200, body: null,
    status(code) { this.code = code; return this; },
    send(body) { this.sent = true; this.body = body; return this; }};
}
function deferredWrite() {
  let release;
  let finished = false;
  let started = false;
  const promise = new Promise(resolve => { release = resolve; });
  return {
    async updateOne() { started = true; await promise; finished = true; },
    release() { release(); },
    get finished() { return finished; },
    get started() { return started; }
  };
}
(async () => {
  const creationWrites = deferredWrite();
  const creation = load('e1f8318b5e403c5ae9807adcf151bef301112f6a', {
    Trip: function () { this.save = cb => cb(null); },
    getPickUpRequests: async () => [{_id: 'pickup-a', status: 'PLANNED'}],
    PickupRequest: creationWrites
  });
  const created = response();
  await creation.createTrip({body: {collectorId: 'collector-a', localities: ['area-a']}}, created);
  assert.equal(creationWrites.started, true);
  assert.equal(created.sent, true);
  assert.equal(creationWrites.finished, false, 'creation responds while pickup writes are pending');
  creationWrites.release();
  await Promise.resolve();
  console.log('CONFIRMED: creation responds before pickup writes finish');

  const cancellationWrites = deferredWrite();
  let callbackDone;
  const cancellation = load('895b370fb518ddc6e49c3828fd87d76270e6fb65', {
    Trip: {
      updateOne(query, update, cb) { callbackDone = cb(null, {n: 1}); },
      async findById() { return {pickups: [{_id: 'pickup-a', status: 'STARTED'}]}; }
    },
    PickupRequest: cancellationWrites
  });
  const cancelled = response();
  cancellation.deleteTrip({params: {id: 'trip-a'}}, cancelled);
  await callbackDone;
  assert.equal(cancellationWrites.started, true);
  assert.equal(cancelled.sent, true);
  assert.equal(cancellationWrites.finished, false, 'cancellation responds while pickup writes are pending');
  cancellationWrites.release();
  await Promise.resolve();
  console.log('CONFIRMED: cancellation responds before pickup writes finish');

  const control = load('3b51f60dc0a48fbadc6d23d5d9ed95a1916b1294', {
    Trip: {findOne(query, cb) { cb(null, {_id: 'trip-a', _doc: {}, pickups: [
      {residentId: 'earlier-done', status: 'DONE'},
      {residentId: 'earlier-started', status: 'STARTED'},
      {residentId: 'target', status: 'STARTED'},
      {residentId: 'later', status: 'STARTED'}
    ]}); }}
  });
  const filtered = response();
  control.getActiveTripForResident({params: {residentId: 'target'}}, filtered);
  assert.deepEqual(Array.from(filtered.body.pickups, p => p.residentId), ['earlier-started', 'target']);
  console.log('CONFIRMED: control filters DONE pickups and preserves the resident prefix');
  console.log('These narrow checks do not establish MongoDB behavior or an exhaustive issue inventory.');
})().catch(error => { console.error(error); process.exitCode = 1; });
