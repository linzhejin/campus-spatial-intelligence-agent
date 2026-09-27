const test = require('node:test');
const assert = require('node:assert/strict');
const { createStore } = require('../../static/js/route-state.js');

const base = {
  schema_version: 1,
  route_id: 'r1',
  route_kind: 'via',
  start: { name: '牌坊' },
  via: { name: '樱顶' },
  end: { name: '图书馆' },
  travel_mode: 'walk',
  hard_constraints: { slope: 'normal' },
  strategy: { name: 'shortest' },
};

test('begin does not mutate committed state and rollback preserves it', () => {
  const store = createStore(base);
  const tx = store.begin({ travel_mode: 'bike' });
  assert.equal(store.current().travel_mode, 'walk');
  assert.equal(store.rollback(tx), true);
  assert.equal(store.commit(tx, { ...base, travel_mode: 'bike' }), false);
  assert.deepEqual(store.current(), base);
});

test('only the newest transaction can commit', () => {
  const store = createStore(base);
  const oldTx = store.begin({ strategy: 'scenery' });
  const newTx = store.begin({ strategy: 'flat' });
  assert.equal(store.commit(oldTx, { ...base, strategy: { name: 'scenery' } }), false);
  assert.equal(store.commit(newTx, { ...base, strategy: { name: 'flat' } }), true);
  assert.equal(store.current().strategy.name, 'flat');
});

test('returned snapshots are clones', () => {
  const store = createStore(base);
  const snapshot = store.current();
  snapshot.start.name = '被篡改';
  assert.equal(store.current().start.name, '牌坊');
});
