import test from 'node:test';
import assert from 'node:assert/strict';
import {SourceLease,PreviewLease,agedSource,matchingSource,sourceEvidence,shouldPreview,browserPreviewMode,backgroundCameraActive} from '../static/console-state.js';
import {DepartureState} from '../static/bus/src/departure-state.js';

test('switching invalidates in-flight inference before cleanup and rejects a late device permission',()=>{
  const lease=new SourceLease();const old=lease.renew(),signal=lease.abort.signal;let stops=0;
  lease.renew();assert.equal(signal.aborted,true);assert.equal(lease.current(old),false);
  assert.equal(lease.acceptStream(old,{getTracks:()=>[{stop:()=>stops++}]}),false);assert.equal(stops,1);assert.equal(lease.stream,null);
});
test('replacing or releasing a browser device stops every track exactly once',()=>{
  const lease=new SourceLease();const token=lease.renew();let stopped=0;
  lease.acceptStream(token,{getTracks:()=>[{stop:()=>stopped++},{stop:()=>stopped++}]});
  lease.releaseStream();lease.releaseStream();assert.equal(stopped,2);
});
test('both source cards keep independent request ownership',()=>{
  const outside=new SourceLease(),inside=new SourceLease();const a=outside.renew(),b=inside.renew();outside.renew();
  assert.equal(outside.current(a),false);assert.equal(inside.current(b),true);assert.equal(inside.abort.signal.aborted,false);
});
test('UI ages the last snapshot instead of holding apparently fresh counts on network loss',()=>{
  const source={kind:'live',connected:true,age_ms:100,status:'connected',session_id:'x'};
  assert.equal(agedSource(source,1399).connected,true);assert.equal(agedSource(source,1400).connected,false);
  assert.equal(agedSource(source,0,false).connected,false);assert.equal(agedSource(source,NaN).connected,false);
  assert.equal(source.age_ms,100);
});
test('image and recorded source provenance is always explicit',()=>{
  assert.match(sourceEvidence({kind:'image',connected:true,age_ms:0}),/^Image test/);
  assert.match(sourceEvidence({kind:'video',connected:true,age_ms:15}),/^Recorded video/);
});
test('recorded video cannot pass a live departure interlock even when posture reports all seated',()=>{
  const gate=new DepartureState();let decision;
  for(let id=0;id<30;id++) decision=gate.update({doors:0,ramp:0,source:{role:'inside',kind:'video',session_id:'recorded',connected:true,frame_id:id,age_ms:0,seating_summary:{captured_at_ms:id*100,age_ms:0,status:'observed',complete:true,people:1,seated:1,standing:0,unknown:0,occupants:[{track_id:1,posture:'seated'}]}}},id*100);
  assert.equal(decision.canDepart,false);
});
test('preview refresh needs a fresh frame and stops when hidden or disconnected',()=>{
  const camera={enabled:true,preview_available:true,preview_frame_id:12};
  assert.equal(shouldPreview(camera,11),true);assert.equal(shouldPreview(camera,12),false);
  assert.equal(shouldPreview(camera,11,false),false);assert.equal(shouldPreview({...camera,enabled:false},11),false);
  assert.equal(shouldPreview({...camera,preview_available:false},11),false);
});

test('an in-flight outside preview cannot revive a paused card or survive close and reopen',async()=>{
  const lease=new PreviewLease();
  const context={kind:'rtsp',sessionId:'outside-a',sourceGeneration:1,paused:false};
  const requested=lease.sync(context);
  let resolve;
  const decoded=new Promise(done=>{resolve=done;});
  let committed=0;
  const fetchAndDecode=decoded.then(()=>{if(lease.current(requested))committed++;});
  lease.sync({...context,paused:true});
  assert.equal(lease.current(requested),false);
  lease.sync(context);
  resolve();await fetchAndDecode;
  assert.equal(committed,0);
  assert.equal(lease.current(lease.sync(context)),true);
});

test('cabin preview ownership survives posture-only refreshes while outside is paused',()=>{
  const inside=new PreviewLease(),outside=new PreviewLease();
  const context={kind:'rtsp',sessionId:'inside-a',sourceGeneration:1,paused:false};
  const requested=inside.sync(context);
  for(const postureEnabled of [false,true,false,true]) {
    outside.sync({...context,sessionId:'outside-a',paused:!postureEnabled});
    // A posture update changes annotations, not the cabin source or gate.
    inside.sync({...context,postureEnabled});
    assert.equal(inside.current(requested),true);
  }
  inside.sync({...context,sessionId:'inside-b'});
  assert.equal(inside.current(requested),false);
  const replacement=inside.sync({...context,sessionId:'inside-b'});
  inside.sync({...context,sessionId:'inside-b',sourceGeneration:2});
  assert.equal(inside.current(replacement),false);
});

test('a reloaded default CCTV card cannot inherit stale image or video bridge history',()=>{
  const source={role:'outside',session_id:'previous-upload',kind:'image',frame_id:1,inference_ms:1136,age_ms:48000};
  const card={role:'outside',kind:'rtsp',camera:{enabled:false},session:null};
  assert.equal(matchingSource(source,card),null);
  assert.equal(matchingSource({...source,kind:'video'},card),null);
  assert.equal(matchingSource({...source,kind:'live'},card),null);
});
test('CCTV evidence requires an enabled camera and the exact matching live session',()=>{
  const source={role:'outside',session_id:'camera-session',kind:'live'};
  const card={role:'outside',kind:'rtsp',camera:{enabled:true,session_id:'camera-session'}};
  assert.equal(matchingSource(source,card),source);
  assert.equal(matchingSource({...source,session_id:'old-session'},card),null);
  assert.equal(matchingSource({...source,kind:'image'},card),null);
  assert.equal(matchingSource(source,{...card,pending:true}),null);
});
test('browser source evidence belongs to its role, source type and current session',()=>{
  const source={role:'inside',session_id:'browser-session',kind:'video'};
  const card={role:'inside',kind:'video',session:'browser-session'};
  assert.equal(matchingSource(source,card),source);
  for(const change of [{role:'outside'},{kind:'image'},{session:null},{session:'replaced-session'}]) assert.equal(matchingSource(source,{...card,...change}),null);
});

test('standing and phrase webcam previews default to live video while retaining a matched frame',()=>{
  for(const options of [{mode:'objects',posture_enabled:true},{mode:'phrase'}]) {
    assert.deepEqual(browserPreviewMode('webcam',options),{selectable:true,retainFrame:true,aligned:false});
    assert.deepEqual(browserPreviewMode('webcam',options,'detection'),{selectable:true,retainFrame:true,aligned:true});
    assert.deepEqual(browserPreviewMode('video',options),{selectable:false,retainFrame:true,aligned:true});
    for(const kind of ['image','rtsp']) assert.deepEqual(browserPreviewMode(kind,options,'detection'),{selectable:false,retainFrame:false,aligned:false});
  }
  assert.deepEqual(browserPreviewMode('webcam',{mode:'objects',posture_enabled:false},'detection'),{selectable:false,retainFrame:false,aligned:false});
});

test('only a running browser camera keeps capture and controller polling active in the background',()=>{
  const camera={kind:'webcam',active:true,hasMedia:true};
  assert.equal(backgroundCameraActive(camera),true);
  for(const role of ['inside','outside']) assert.equal(backgroundCameraActive({...camera,role}),true);
  for(const kind of ['image','video','rtsp']) assert.equal(backgroundCameraActive({...camera,kind}),false);
  assert.equal(backgroundCameraActive({...camera,active:false}),false);
  assert.equal(backgroundCameraActive({...camera,hasMedia:false}),false);
  assert.equal(backgroundCameraActive(null),false);
});
