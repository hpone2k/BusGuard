import test from 'node:test';
import assert from 'node:assert/strict';
import {posturePresentation} from '../static/posture-status.js';

function state(patch={}) {
  return {controller:{vehicle:{doors:'closed',revalidation_required:false}},online:true,elapsedMs:0,
    source:{connected:true,session_id:'inside-1',age_ms:0},enabled:true,requested:true,hasInput:true,
    model:{available:true,engine:'YOLOE'},summary:{status:'observed',engine:'YOLOE',mode:'standing_only',
      age_ms:0,seated:null,standing:0,unknown:0,clear:true,complete:true,standing_check_valid:true,
      occupants:[{posture:'not_detected',bbox:[.1,.1,.5,.9]}],reason:'No standing detected.'},...patch};
}

test('restart hold explains unknown doors and disconnected camera instead of claiming active counting',()=>{
  const view=posturePresentation(state({controller:{vehicle:{doors:'unknown',revalidation_required:true}},source:null,hasInput:false}));
  assert.equal(view.status,'held');assert.match(view.message,/Restart hold/);
  assert.match(view.detail,/camera also needs to be connected/);
  assert.match(view.detail,/Reset held simulation/);
  assert.equal(view.standing,null);assert.equal(view.observed,false);
});

test('unknown door state cannot display posture even when a prior seated frame remains',()=>{
  const view=posturePresentation(state({controller:{vehicle:{doors:'unknown'}}}));
  assert.match(view.message,/Door state unconfirmed/);assert.equal(view.standing,null);
});

test('opening, open and closing gates keep posture unavailable and explain counting separately',()=>{
  for(const doors of ['opening','open','closing']) {
    const view=posturePresentation(state({controller:{vehicle:{doors}}}));
    assert.equal(view.status,'paused');assert.equal(view.standing,null);
    assert.match(view.detail,/Person counting continues/);
    assert.match(view.detail,/only after.*fully closed/);
  }
});

test('checkbox drafts do not claim the running source has adopted a setting',()=>{
  for(const requested of [true,false]) {
    const view=posturePresentation(state({requested,enabled:!requested}));
    assert.equal(view.status,'draft');assert.match(view.message,/apply to activate/);
    assert.match(view.detail,new RegExp(`running source still has posture ${requested?'off':'on'}`));
    assert.equal(view.observed,false);
  }
  assert.equal(posturePresentation(state({enabled:false,requested:false})).status,'disabled');
});

test('standing threshold drafts wait for apply and never replace running evidence silently',()=>{
  const draft=posturePresentation(state({confidence:.10,requestedConfidence:.17}));
  assert.equal(draft.status,'draft');assert.equal(draft.standing,null);assert.equal(draft.observed,false);
  assert.match(draft.detail,/still uses 0.10/);assert.match(draft.detail,/use 0.17/);
  assert.equal(posturePresentation(state({confidence:.17,requestedConfidence:.17})).status,'observed');
  assert.equal(posturePresentation(state({enabled:false,requested:false,confidence:.10,requestedConfidence:.17})).status,'disabled');
});

test('disconnected camera, stale observations and missing model have distinct explanations',()=>{
  assert.equal(posturePresentation(state({hasInput:false,source:null})).status,'disconnected');
  assert.match(posturePresentation(state({hasInput:false,source:null})).detail,/checkbox alone does not start/);
  assert.equal(posturePresentation(state({source:{session_id:'old',connected:false,age_ms:1500}})).status,'stale');
  const missing=posturePresentation(state({model:{available:false,error:'Local weights are missing.'}}));
  assert.equal(missing.status,'unavailable');assert.equal(missing.detail,'Local weights are missing.');
  assert.equal(posturePresentation(state({eligible:false})).status,'disabled');
});

test('controller offline or outdated state never displays retained posture as live',()=>{
  for(const patch of [{online:false},{elapsedMs:1500},{elapsedMs:-1},{elapsedMs:NaN}]) {
    const view=posturePresentation(state(patch));assert.equal(view.status,'offline');assert.equal(view.standing,null);
  }
  assert.equal(posturePresentation(state({summary:{status:'observed',age_ms:1000,seated:2}})).status,'stale');
});

test('live status includes only standing counts and retains uncertainty reasons',()=>{
  const summary={status:'observed',engine:'YOLOE',mode:'standing_only',age_ms:0,seated:null,standing:1,unknown:1,
    complete:false,clear:false,occupants:[{posture:'not_detected',bbox:[.1,.1,.2,.9]},{posture:'standing',bbox:[.3,.1,.4,.9]},
      {posture:'unknown',reason:'Person cannot be classified reliably.',bbox:[.5,.1,.6,.9]}]};
  const view=posturePresentation(state({summary}));
  assert.equal(view.standing,1);assert.equal(view.clear,false);
  assert.equal(Object.hasOwn(view,'seated'),false);assert.equal(Object.hasOwn(view,'unknown'),false);
  assert.match(view.message,/Live standing detection · 1 standing/);
  assert.match(view.detail,/Standing detection is incomplete/);assert.match(view.detail,/not added to the person total/);
  assert.doesNotMatch(view.detail,/joint|MediaPipe/i);
});

test('test media diagnostics stay labelled and cannot imply live departure clearance',()=>{
  for(const kind of ['image','video']) {
    const view=posturePresentation(state({kind}));
    assert.match(view.message,/Test standing detection/);assert.match(view.detail,/cannot clear live departure/);
  }
});

test('backend paused and unavailable reasons appear directly rather than an empty timer',()=>{
  const view=posturePresentation(state({summary:{status:'unavailable',reason:'LocateAnything failed for this frame.'}}));
  assert.equal(view.status,'unavailable');assert.equal(view.message,'LocateAnything failed for this frame.');
  assert.equal(view.standing,null);
});

test('LocateAnything inference age is separate from fresh generic object frames',()=>{
  const summary={status:'observed',engine:'LocateAnything',age_ms:1300,posture_frame_id:42,
    seated:1,standing:0,unknown:0,occupants:[]};
  const view=posturePresentation(state({summary,elapsedMs:200}));
  assert.equal(view.status,'observed');assert.equal(view.ageMs,1500);
  assert.equal(posturePresentation(state({summary:{...summary,engine:'MediaPipe'}})).status,'stale');
  // A newer generic frame cannot refresh the same older posture result.
  const expired=posturePresentation(state({source:{session_id:'inside',connected:true,age_ms:0},
    summary:{...summary,age_ms:3900},elapsedMs:100}));
  assert.equal(expired.status,'stale');assert.equal(expired.standing,null);
});

test('a complete clear standing-only check never claims sitting was detected',()=>{
  const view=posturePresentation(state());
  assert.equal(view.clear,true);assert.equal(view.engine,'YOLOE');assert.equal(view.standing,0);
  assert.match(view.detail,/5 continuous seconds of fresh observations after the doors close/);
  assert.doesNotMatch(view.message+' '+view.detail,/confirmed seated|detected sitting|sitting ·/i);
  for(const patch of [{standing_check_valid:false},{standing_check_valid:undefined},{clear:false},{mode:'legacy',seated:99}]) {
    const incomplete=posturePresentation(state({summary:{...state().summary,...patch}}));
    assert.equal(incomplete.clear,false);assert.equal(Object.hasOwn(incomplete,'seated'),false);
  }
});

test('a valid no-standing pass ignores unresolved person IDs and uses the controller confirmation duration',()=>{
  const view=posturePresentation(state({controller:{vehicle:{doors:'closed'},readiness:{posture:{confirmation_ms:6500}}},
    summary:{...state().summary,unknown:2,complete:false,
      occupants:[{posture:'unknown',reason:'Previously observed person is missing.'}]}}));
  assert.equal(view.clear,true);
  assert.match(view.detail,/6.5 continuous seconds/);
  assert.doesNotMatch(view.detail,/missing|unresolved|incomplete/);
});

test('YOLOE uses normal same-frame freshness and model metadata names the active engine',()=>{
  assert.equal(posturePresentation(state({summary:{...state().summary,age_ms:999}})).status,'observed');
  assert.equal(posturePresentation(state({summary:{...state().summary,age_ms:1000}})).status,'stale');
  const model={available:false,engine:'YOLOE',error:'Model unavailable'};
  assert.match(posturePresentation(state({model})).message,/YOLOE standing detector unavailable/);
  assert.equal(posturePresentation(state({model:{available:true,engine:'Custom'}})).engine,'Custom');
});
