import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {AnalyzedFrame,captureDimensions,contentRect,colorFor,detectionScoreLabel,liveBoxes} from '../static/core.js';
import {SourceLease,browserPreviewMode,backgroundCameraActive} from '../static/console-state.js';
import {detectionPausedFor,postureVisible} from '../static/journey-detection.js';
import {drawPosture} from '../static/posture.js';

// Run the real card methods without starting the console or acquiring hardware.
const source=readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
const cardSource=source.slice(source.indexOf('class InputCard {'),source.indexOf('const cards ='));
const pollSource=source.slice(source.indexOf('async function pollState()'),source.indexOf('async function pollCameras()'));

function canvas() {
  const calls={images:[],boxes:[],labels:[]};
  const ctx={calls,setTransform(){},clearRect(){},drawImage(...args){calls.images.push(args);},
    strokeRect(...args){calls.boxes.push(args);},setLineDash(){},save(){},restore(){},
    measureText(text){return {width:text.length*5};},fillRect(){},fillText(text){calls.labels.push(text);}};
  return {width:0,height:0,getContext:()=>ctx,ctx,
    toBlob(callback){callback({type:'image/jpeg'});}};
}

function cameraHarness({kind='webcam',role='inside',doors='closed',inferenceMs=900,postureAge=inferenceMs}={}) {
  let now=0,requests=0,inFlight=0,maxInFlight=0,card;
  const waits=[],errors=[];
  const app={snapshot:{assistance:{vehicle:{doors,motion:'stationary'}}},online:true,received:0};
  const document={hidden:true,createElement:()=>canvas()};
  const InputCard=runInNewContext(`(${cardSource})`,{
    document,window:{devicePixelRatio:1},performance:{now:()=>now},app,
    browserPreviewMode,backgroundCameraActive,captureDimensions,contentRect,colorFor,
    detectionScoreLabel,liveBoxes,drawPosture,postureVisible,
    detectionPaused:cameraRole=>detectionPausedFor(cameraRole,app.snapshot.assistance,true,0),
    wait:async ms=>{waits.push(ms);card.active=false;},
    api:async()=>{
      requests++;inFlight++;maxInFlight=Math.max(maxInFlight,inFlight);
      await Promise.resolve();now+=inferenceMs;inFlight--;
      if(requests===2)card.active=false;
      return {frame_id:requests,detections:[{label:'person',bbox:[.1,.1,.5,.9],score:.9}],
        seating_summary:{status:'observed',age_ms:postureAge,occupants:[{posture:'standing',bbox:[.1,.1,.5,.9]}]}};
    }
  });
  card=Object.assign(Object.create(InputCard.prototype),{
    kind,role,active:true,hasMedia:true,lease:new SourceLease(),session:'test-camera',
    sessionOptions:{mode:'objects',size:640,posture_enabled:true},analyzed:new AnalyzedFrame(),
    frameId:0,lastMediaTime:-1,processed:canvas(),
    el:{'preview-view':{value:'live'},video:{videoWidth:1280,videoHeight:720,currentTime:1,readyState:4,hidden:false},
      image:{hidden:true},overlay:canvas(),stage:{getBoundingClientRect:()=>({width:640,height:360})},
      placeholder:{hidden:true},'empty-title':{},'empty-copy':{}},
    status(){},controls(){},error:message=>errors.push(message),deleteSession:async()=>{}
  });
  card.processedCtx=card.processed.getContext('2d');
  return {card,app,waits,errors,stats:()=>({now,requests,maxInFlight})};
}

test('hidden live capture stays serial and retains matched frames without freezing the live video',async()=>{
  const {card,app,waits,errors,stats}=cameraHarness();
  await card.loop(0,card.session);
  assert.deepEqual(errors,[]);
  assert.deepEqual(stats(),{now:1800,requests:2,maxInFlight:1});
  assert.deepEqual(waits,[]); // A background timer must not add seconds after inference.
  assert.equal(card.analyzed.current(1800).frameId,2);
  assert.equal(card.processed.ctx.calls.images.length,2);

  app.received=1800;
  card.draw(1850);
  assert.equal(card.el.video.hidden,false);
  assert.equal(card.el.overlay.ctx.calls.images.length,0);
  assert.equal(card.el.overlay.ctx.calls.boxes.length,0); // 950 ms results cannot label current pixels.

  card.el['preview-view'].value='detection';
  card.draw(1850);
  assert.equal(card.el.video.hidden,true);
  assert.equal(card.el.overlay.ctx.calls.images[0][0],card.processed);
  assert.ok(card.el.overlay.ctx.calls.boxes.length>0);
  const boxes=card.el.overlay.ctx.calls.boxes.length;
  card.draw(2500);
  assert.equal(card.el.overlay.ctx.calls.boxes.length,boxes); // Expired evidence is never revived.

  card.el['preview-view'].value='live';
  card.draw(2500);
  assert.equal(card.el.video.hidden,false);
});

test('background capture preserves the outside door gate and does not run uploaded video',async()=>{
  for(const options of [{role:'outside',doors:'closed'},{kind:'video'}]) {
    const {card,stats,waits,errors}=cameraHarness(options);
    await card.loop(0,card.session);
    assert.deepEqual(errors,[]);
    assert.equal(stats().requests,0);
    assert.deepEqual(waits,[200]);
  }
  const {card,stats}=cameraHarness({role:'outside',doors:'open'});
  await card.loop(0,card.session);
  assert.equal(stats().requests,2);
});

test('live video shows fresh standing boxes only within the object overlay lifetime and closed-door gate',async()=>{
  const {card,app}=cameraHarness({inferenceMs:50});
  await card.loop(0,card.session);
  app.received=100;
  const labels=card.el.overlay.ctx.calls.labels;
  card.draw(150);
  assert.equal(card.el.video.hidden,false);
  assert.ok(labels.includes('Standing person'));
  labels.length=0;
  card.draw(300); // Latest capture was at 50 ms: exactly the live-box limit.
  assert.ok(labels.includes('Standing person'));
  labels.length=0;
  card.draw(301);
  assert.equal(labels.length,0);

  app.snapshot.assistance.vehicle.doors='open';
  card.draw(150);
  assert.equal(labels.includes('Standing person'),false);
  assert.ok(labels.some(label=>label.startsWith('person'))); // Occupancy stays active.
});

test('live standing boxes age server evidence from receipt without extending the posture lifetime',async()=>{
  const {card,app}=cameraHarness({inferenceMs:50,postureAge:950});
  await card.loop(0,card.session);
  app.received=100;
  const labels=card.el.overlay.ctx.calls.labels;
  card.draw(149); // Received at 100 ms: 950 + 49 ms is still fresh.
  assert.ok(labels.includes('Standing person'));
  labels.length=0;
  card.draw(150);
  assert.equal(labels.includes('Standing person'),false);
  assert.ok(labels.some(label=>label.startsWith('person')));
});

test('hidden controller polling continues for live cameras and sleeps for other sources',async()=>{
  for(const [kind,active,expected] of [['webcam',true,1],['webcam',false,0],['video',true,0],['rtsp',true,0]]) {
    const requests=[],delays=[];
    const app={};
    const poll=runInNewContext(`(${pollSource})`,{
      document:{hidden:true},cards:[{kind,active,hasMedia:true,syncPreview(){}}],app,
      backgroundCameraActive,AbortSignal,performance:{now:()=>100},
      api:async url=>{requests.push(url);return {sources:{}};},
      setTimeout:(_,delay)=>delays.push(delay)
    });
    await poll();
    assert.equal(requests.length,expected);
    assert.deepEqual(delays,[500]);
    if(expected)assert.equal(app.online,true);
  }
});
