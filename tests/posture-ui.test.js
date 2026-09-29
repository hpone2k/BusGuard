import test from 'node:test';
import assert from 'node:assert/strict';
import {postureAllowed,drawPosture,validPostureBox,postureFrameFresh} from '../static/posture.js';

test('posture checkbox requires inside occupancy categories, never clothing subsets',()=>{
  assert.equal(postureAllowed('inside','objects','person, wheelchair'),true);
  assert.equal(postureAllowed('outside','objects','person'),false);
  assert.equal(postureAllowed('inside','phrase','person'),false);
  assert.equal(postureAllowed('inside','objects','person with red shirt'),false);
});

function canvas() {
  const calls={boxes:[],labels:[]};
  return {calls,ctx:{save(){},restore(){},setLineDash(){},strokeRect(...box){calls.boxes.push(box);},
    measureText(label){return {width:label.length*5};},fillRect(){},fillText(label){calls.labels.push(label);},
    arc(){throw new Error('Standing detection must not draw skeleton joints');},lineTo(){throw new Error('No skeleton lines');}}};
}
const rect={x:10,y:20,w:200,h:100};
const summary={status:'observed',age_ms:0,occupants:[{posture:'standing',track_id:1,bbox:[.2,.2,.4,.8]}]};

test('posture overlay labels the exact matched person box and does not draw joints',()=>{
  const {ctx,calls}=canvas();drawPosture(ctx,summary,rect);
  assert.deepEqual(calls.boxes[0],[50,40,40,60.00000000000001]);
  assert.equal(calls.labels[0],'Standing person');
  for(const posture of ['seated','unknown','not_detected',undefined]) {
    const view=canvas();drawPosture(view.ctx,{...summary,occupants:[{...summary.occupants[0],posture}]},rect);
    assert.equal(view.calls.labels.length,0);assert.equal(view.calls.boxes.length,0);
  }
});

test('only observed and fresh summaries draw posture boxes',()=>{
  for(const status of ['stale','paused','disabled','unavailable','awaiting_fresh_frame',undefined]) {
    const {ctx,calls}=canvas();drawPosture(ctx,{...summary,status},rect);
    assert.equal(calls.boxes.length,0);assert.equal(calls.labels.length,0);
  }
  for(const age_ms of [-1,1000,Infinity,NaN]) {
    const {ctx,calls}=canvas();drawPosture(ctx,{...summary,age_ms},rect);assert.equal(calls.boxes.length,0);
  }
});

test('legacy or unassigned joints never manufacture a standing box',()=>{
  const {ctx,calls}=canvas();drawPosture(ctx,{...summary,occupants:[{posture:'standing',landmarks:[{x:.2,y:.3}]}],
    unassigned_poses:[{landmarks:[{x:.2,y:.3}]}]},rect);
  assert.equal(calls.boxes.length,0);assert.equal(calls.labels.length,0);
});

test('malformed, predicted and offscreen rectangles are not extrapolated',()=>{
  for(const bbox of [null,[0,0,1],[-.1,0,1,1],[0,0,1.1,1],[0,0,NaN,1],[0,0,'1',1],[1,0,0,1]]) {
    assert.equal(validPostureBox(bbox),false);
    const {ctx,calls}=canvas();drawPosture(ctx,{...summary,occupants:[{posture:'standing',bbox}]},rect);
    assert.equal(calls.boxes.length,0);
  }
  const {ctx,calls}=canvas();drawPosture(ctx,{...summary,occupants:[{...summary.occupants[0],predicted:true}]},rect);
  assert.equal(calls.boxes.length,0);
});

test('LocateAnything latency is accepted only within its explicit four second lifetime',()=>{
  for(const [engine,age_ms,visible] of [['LocateAnything',1300,true],['LocateAnything',3999,true],
    ['LocateAnything',4000,false],['YOLOE',999,true],['YOLOE',1000,false],['MediaPipe',1300,false],[undefined,1300,false]]) {
    const {ctx,calls}=canvas();drawPosture(ctx,{...summary,engine,age_ms},rect);
    assert.equal(calls.boxes.length>0,visible);
  }
});

test('cached preview switches away from posture exactly when its inference evidence expires',()=>{
  const delayed={...summary,engine:'LocateAnything',age_ms:1300,posture_frame_id:5};
  assert.equal(postureFrameFresh(delayed,2699),true);
  assert.equal(postureFrameFresh(delayed,2700),false);
  assert.equal(postureFrameFresh({...delayed,age_ms:4000},0),false);
  assert.equal(postureFrameFresh({...delayed,status:'paused'},0),false);
});
