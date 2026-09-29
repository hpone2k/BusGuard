import {test} from 'node:test';
import assert from 'node:assert/strict';
import {detectionPausedFor, postureVisible} from '../static/journey-detection.js';

test('inside person counting continues throughout travel and controller reconnects', () => {
  const state = {vehicle:{motion:'moving',doors:'closed'},scenario: {enabled: true, detection_enabled: false}};
  assert.equal(detectionPausedFor('inside', state), false);
  assert.equal(detectionPausedFor('inside', state, false, 5000), false);
  assert.equal(detectionPausedFor('outside', state), true);
  state.scenario.detection_enabled = true;
  state.vehicle={motion:'stationary',doors:'open'};
  assert.equal(detectionPausedFor('outside', state), false);
  assert.equal(detectionPausedFor('outside', state, true, 1600), true);
});

test('outside detection waits for fully open doors in both manual and scenario modes',()=>{
  for(const enabled of [false,true]) {
    for(const doors of ['unknown','opening','closing','closed',undefined]) {
      const state={scenario:{enabled,detection_enabled:true},vehicle:{motion:'stationary',doors}};
      assert.equal(detectionPausedFor('outside',state),true);
      assert.equal(detectionPausedFor('inside',state),false);
    }
    const open={scenario:{enabled},vehicle:{motion:'stationary',doors:'open'}};
    assert.equal(detectionPausedFor('outside',open),false);
    for(const motion of ['moving','braking','unknown',undefined]) {
      assert.equal(detectionPausedFor('outside',{...open,vehicle:{motion,doors:'open'}}),true);
    }
    for(const [online,age] of [[false,0],[true,1500],[true,-1],[true,NaN]]) {
      assert.equal(detectionPausedFor('outside',open,online,age),true);
      assert.equal(detectionPausedFor('inside',open,online,age),false);
    }
  }
  assert.equal(detectionPausedFor('outside',null),true);
});

test('posture overlay only displays with fresh closed-door state', () => {
  for (const doors of ['opening', 'open', 'closing', 'unknown']) {
    assert.equal(postureVisible({vehicle: {doors}}), false);
  }
  const closed = {vehicle: {doors: 'closed'}};
  assert.equal(postureVisible(closed), true);
  assert.equal(postureVisible(closed, false), false);
  assert.equal(postureVisible(closed, true, 1500), false);
});
