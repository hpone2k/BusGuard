import test from 'node:test';
import assert from 'node:assert/strict';
import { Generation, ConfidenceChoices, ConfidenceProfiles, PromptDrafts, ResolutionProfiles, AnalyzedFrame, captureDimensions, detectionScoreLabel, abortable, loadVideo, evidenceLabels, inferencePresentation, promptCategories, validatePrompt, boxesAtTime, liveBoxes, contentRect } from '../static/core.js';
import {countPresentation} from '../static/counts.js';

test('source changes abort requests and invalidate previous responses', () => {
  const generation = new Generation();
  const old = generation.next(), signal = generation.abort.signal;
  const current = generation.next();
  assert.equal(signal.aborted, true);
  assert.equal(generation.current(old), false);
  assert.equal(generation.current(current), true);
});

test('timeline never interpolates across a long detection gap', () => {
  const det = {label:'person', track_id:1, bbox:[.1,.1,.2,.2]};
  const rows = [{time:0,detections:[det]}, {time:10,detections:[det]}];
  assert.deepEqual(boxesAtTime(rows, 5), []);
  assert.deepEqual(boxesAtTime(rows, -1), []);
});

test('interpolation uses stable IDs rather than the array order', () => {
  const a = {label:'person',track_id:1,bbox:[0,0,.2,.2]};
  const b = {label:'person',track_id:2,bbox:[.7,0,.9,.2]};
  const rows = [{time:0,detections:[a,b]}, {time:.2,detections:[{...b,bbox:[.6,0,.8,.2]},{...a,bbox:[.1,0,.3,.2]}]}];
  assert.equal(boxesAtTime(rows,.1)[0].bbox[0], .05);
  assert.deepEqual(boxesAtTime([{time:0,detections:[a]}, {time:.1,detections:[]}],.1), []);
});

test('overlay respects letterboxing', () => {
  assert.deepEqual(contentRect(800,600,1920,1080), {x:0,y:75,w:800,h:450});
  assert.deepEqual(contentRect(800,600,600,1200), {x:250,y:0,w:300,h:600});
});

test('live compensation is bounded and cannot resurrect old boxes', () => {
  const box = {bbox:[.2,.2,.4,.4], velocity:[2,0,2,0], observed_at_ms:1000};
  assert.ok(Math.abs(liveBoxes([box],1000,1100)[0].bbox[0] - .25) < 1e-9);
  assert.deepEqual(liveBoxes([box],1000,1300), []);
  assert.deepEqual(liveBoxes([box],1000,900), []);
  assert.deepEqual(liveBoxes([box],1000,1100,{compensate:false})[0].bbox, box.bbox);
});

test('missing-box recovery includes transport time and is not extrapolated twice', () => {
  const box = {bbox:[.2,.2,.4,.4], velocity:[1,0,1,0], predicted:true, observed_at_ms:1000};
  assert.deepEqual(liveBoxes([box],1100,1140)[0].bbox, box.bbox);
  assert.deepEqual(liveBoxes([box],1100,1160), []);
  assert.deepEqual(liveBoxes([box],1100,1110,{bridgeMs:100}), []);
});

test('offline playback does not extend the missing-box prediction deadline', () => {
  const rows = [{time:.1, detections:[{label:'cat', bbox:[.1,.1,.2,.2], predicted:true, observed_at_ms:0}]}];
  assert.equal(boxesAtTime(rows,.1).length,1);
  assert.deepEqual(boxesAtTime(rows,.2), []);
});

test('responsive prediction expiry is identical in live and recorded playback', () => {
  const d = {label:'cat', bbox:[.1,.1,.2,.2], predicted:true, observed_at_ms:100, prediction_expires_at_ms:200};
  assert.deepEqual(liveBoxes([d],170,210), []);
  assert.deepEqual(boxesAtTime([{time:.17,detections:[d]}],.21), []);
});

test('each prompt type keeps its own confidence when types are added or reordered', () => {
  const choices = new ConfidenceChoices();
  choices.sync('person, laptop'); choices.set('person', .8); choices.set('laptop', .2);
  assert.deepEqual(choices.sync(' laptop, PERSON, bottle '), [
    {label:'laptop',value:.2}, {label:'PERSON',value:.8}, {label:'bottle',value:.35}]);
  assert.deepEqual(choices.toJSON(), {laptop:.2, PERSON:.8, bottle:.35});
  choices.sync('PERSON, person, bottle');
  assert.deepEqual(choices.toJSON(), {PERSON:.8,bottle:.35});
  choices.sync(''); choices.sync('laptop, person');
  assert.deepEqual(choices.toJSON(), {laptop:.2,person:.8});
});

test('saved per-type choices and legacy single thresholds both restore correctly', () => {
  const choices = new ConfidenceChoices();
  choices.restore('Person, laptop, bottle','objects',{person:.8,laptop:.2},.4);
  assert.deepEqual(choices.toJSON(), {Person:.8,laptop:.2,bottle:.4});
  choices.restore('person, laptop','objects',{},.6);
  assert.deepEqual(choices.toJSON(), {person:.6,laptop:.6});
});

test('empty prompts, phrases, and unusual labels do not create duplicate or unsafe controls', () => {
  assert.deepEqual(promptCategories(' , , '), []);
  assert.deepEqual(promptCategories('person, wearing red', 'phrase'), ['person, wearing red']);
  const choices = new ConfidenceChoices();
  choices.sync('__proto__, person'); choices.set('__proto__', .7);
  assert.equal(choices.toJSON()['__proto__'], .7);
  assert.equal(Object.getPrototypeOf(choices.toJSON()), Object.prototype);
  assert.throws(() => choices.set('person', NaN));
});

test('multiple detailed phrases retain full labels and independent confidence', () => {
  const red = 'a person with a red shirt', blue = 'a person wearing blue, carrying a bag';
  const prompt = ` ${red}\r\n${blue};${red.toUpperCase()};\n`;
  assert.deepEqual(promptCategories(prompt, 'phrase'), [red,blue]);
  const choices = new ConfidenceChoices();
  choices.sync(prompt, 'phrase'); choices.set(red,.6); choices.set(blue,.25);
  choices.sync(`${blue};${red}`, 'phrase');
  assert.deepEqual(choices.toJSON(), {[blue]:.25,[red]:.6});
});

test('phrase limits reject excess targets without silently trimming them', () => {
  const prompt = Array.from({length:8},(_,i)=>`a target ${i}`).join('\n');
  assert.equal(validatePrompt(prompt,'phrase').length,8);
  assert.throws(()=>validatePrompt(`${prompt};a ninth target`,'phrase'),/at most 8/);
  assert.throws(()=>validatePrompt('x'.repeat(161),'phrase'),/160 characters/);
  assert.throws(()=>validatePrompt('; \n ;','phrase'),/at least one/);
  assert.throws(()=>validatePrompt('<target>','phrase'),/plain text/);
  assert.equal(validatePrompt('person, laptop','objects').length,2);
  assert.equal(validatePrompt('a person, wearing red','phrase').length,1);
});

test('switching target modes preserves both edited drafts and confidence profiles', () => {
  const drafts = new PromptDrafts('person, laptop');
  assert.match(drafts.switch('phrase','person, laptop, bag'),/red shirt\n/);
  const phrases = 'a blue bag\na red backpack';
  assert.equal(drafts.switch('objects',phrases),'person, laptop, bag');
  assert.equal(drafts.switch('phrase','person, laptop'),'a blue bag\na red backpack');
  drafts.restore('phrase','a person walking');
  drafts.switch('objects','a person walking');
  assert.equal(drafts.switch('phrase','wheelchair'),'a person walking');

  const profiles = new ConfidenceProfiles();
  const objects = profiles.forMode('objects'), detailed = profiles.forMode('phrase');
  objects.sync('person');objects.set('person',.75);
  detailed.sync('person','phrase');detailed.set('person',.25);
  assert.deepEqual(profiles.forMode('objects').toJSON(),{person:.75});
  assert.deepEqual(profiles.forMode('phrase').toJSON(),{person:.25});
  assert.equal(detailed.sync('a new detailed target','phrase')[0].value,.3);
});

test('phrase and object resolutions have independent defaults and retain edits', () => {
  const profiles = new ResolutionProfiles();
  assert.equal(profiles.switch('phrase',640),512);
  assert.equal(profiles.switch('objects',960),640);
  assert.equal(profiles.switch('phrase',384),960);
  profiles.restore('phrase',640);
  assert.equal(profiles.switch('objects',640),384);
  assert.equal(profiles.switch('phrase',384),640);
});

test('analyzed phrase frames retain aligned observations across slow inference without extrapolating', () => {
  const frames = new AnalyzedFrame();frames.reset(3);
  const observed = {label:'a red shirt',bbox:[.1,.2,.4,.8],velocity:[10,10,10,10],predicted:false};
  const predicted = {...observed,predicted:true};
  assert.equal(frames.accept({capturedAt:100,frameId:1,detections:[observed,predicted]},3,650),true);
  assert.deepEqual(frames.current(950).detections,[observed]);
  assert.deepEqual(frames.current(950).detections[0].bbox,[.1,.2,.4,.8]);
  assert.equal(frames.current(1600),null);
  assert.equal(frames.current(1000),null); // An expired snapshot never reappears.
});

test('source changes reject late analyzed frames and clear the retained snapshot immediately', () => {
  const frames = new AnalyzedFrame();frames.reset(1);
  const frame = {capturedAt:10,frameId:1,detections:[]};
  assert.equal(frames.accept(frame,1,200),true);
  frames.reset(2);
  assert.equal(frames.current(200),null);
  assert.equal(frames.accept(frame,1,300),false);
  assert.equal(frames.current(300),null);
  assert.equal(frames.accept({...frame,capturedAt:400},2,700),true);
});

test('slow cabin inference retains the last image without reviving expired boxes or posture',()=>{
  const frames=new AnalyzedFrame();frames.reset(1);
  const frame={capturedAt:0,frameId:10,width:640,height:360,
    detections:[{label:'person',bbox:[.1,.1,.5,.9],predicted:false}],
    seating:{status:'observed',standing:1}};
  assert.equal(frames.accept(frame,1,1200),true);
  assert.equal(frames.preview(1400).fresh,true);
  assert.equal(frames.preview(1800).fresh,false);
  assert.deepEqual(frames.preview(1800).frame,{capturedAt:0,frameId:10,width:640,height:360});
  assert.equal(frames.current(1800),null);
  assert.equal(frames.preview(1900).frame.detections,undefined);
  assert.equal(frames.preview(1900).frame.seating,undefined);
  assert.equal(frames.accept({...frame,capturedAt:1250,frameId:11},1,2450),true);
  assert.equal(frames.preview(2500).fresh,true);
  assert.equal(frames.preview(2500).frame.frameId,11);
  frames.reset(2);
  assert.equal(frames.preview(2500),null);
});

test('analyzed frames replace the latest slot and reject stale or out of order completions', () => {
  const frames = new AnalyzedFrame();frames.reset(1);
  const frame = {capturedAt:200,frameId:2,detections:[]};
  assert.equal(frames.accept(frame,1,450),true);
  assert.equal(frames.accept({...frame,capturedAt:100,frameId:1},1,500),false);
  assert.equal(frames.current(500).frameId,2);
  assert.equal(frames.accept({...frame,capturedAt:400,frameId:3},1,750),true);
  assert.equal(frames.current(750).frameId,3);
  assert.equal(frames.accept({...frame,capturedAt:600},1,2100),false);
  assert.equal(frames.accept({...frame,capturedAt:1000},1,900),false);
  assert.equal(frames.accept({...frame,capturedAt:NaN},1,900),false);
});

test('phrase capture retains short-edge detail and bounds the long edge without upscaling', () => {
  assert.deepEqual(captureDimensions(1920,1080,512,'phrase'),{width:910,height:512});
  assert.deepEqual(captureDimensions(1080,1920,512,'phrase'),{width:512,height:910});
  assert.deepEqual(captureDimensions(4000,1000,512,'phrase'),{width:1024,height:256});
  assert.deepEqual(captureDimensions(320,240,512,'phrase'),{width:320,height:240});
  assert.deepEqual(captureDimensions(1920,1080,512,'objects'),{width:512,height:288});
  assert.deepEqual(captureDimensions(320,240,640,'objects'),{width:320,height:240});
  assert.throws(()=>captureDimensions(0,240,512,'phrase'),/positive/);
});

test('phrase boxes use decimal match scores while object boxes retain percentages', () => {
  assert.equal(detectionScoreLabel(.427,'phrase'),' 0.43');
  assert.equal(detectionScoreLabel(.427,'objects'),' 43%');
  assert.equal(detectionScoreLabel(null,'phrase'),'');
  assert.equal(detectionScoreLabel(NaN,'phrase'),'');
});

test('changing source cancels pending image decode without waiting for its completion', async () => {
  const controller = new AbortController();let finish;
  const decoding = new Promise(resolve=>{finish=resolve;});
  const pending = abortable(decoding,controller.signal);
  controller.abort();
  await assert.rejects(pending,{name:'AbortError'});
  finish('old image');
  assert.equal(await abortable(Promise.resolve('new image'),new AbortController().signal),'new image');
});

test('aborted video loading removes handlers so a replacement source owns decoder events', async () => {
  class Video extends EventTarget {load() {this.loads = (this.loads || 0)+1;}}
  const video = new Video(), old = new AbortController();
  const first = loadVideo(video,old.signal);old.abort();
  await assert.rejects(first,{name:'AbortError'});
  const current = loadVideo(video,new AbortController().signal);
  video.dispatchEvent(new Event('loadeddata'));
  await current;
  assert.equal(video.loads,2);
  // A later decoder event must not re-settle either former operation.
  video.dispatchEvent(new Event('error'));
});

test('video decode failures and pre-aborted loads do not leave waiting operations', async () => {
  class Video extends EventTarget {load() {this.loads = (this.loads || 0)+1;}}
  const video = new Video(), controller = new AbortController();controller.abort();
  await assert.rejects(loadVideo(video,controller.signal),{name:'AbortError'});
  assert.equal(video.loads,undefined);
  const loading = loadVideo(video,new AbortController().signal);
  video.dispatchEvent(new Event('error'));
  await assert.rejects(loading,/cannot decode/);
});

test('editing unapplied phrases cannot introduce new labels into existing detection evidence', () => {
  const applied = {mode:'phrase',prompt:'a red shirt\na blue bag\na wheelchair'};
  const draft = {mode:'phrase',prompt:Array.from({length:9},(_,i)=>`draft target ${i}`).join('\n')};
  const result = {detections:[{label:'a red shirt',bbox:[0,0,.5,.5],score:.5}]};
  const labels = evidenceLabels(result,applied,draft);
  const view = countPresentation(null,{mode:'image',detections:result.detections,labels});
  assert.deepEqual(Object.keys(view.classes),['a red shirt','a blue bag','a wheelchair']);
  assert.equal(view.total.raw,1);
  assert.equal(evidenceLabels(null,applied,draft).length,9);
  assert.deepEqual(evidenceLabels(result,null,draft),[]);
});

test('RTSP evidence uses connected camera settings while its editor can contain a different mode', () => {
  const cameraOptions = {mode:'objects',prompt:'person, wheelchair'};
  const draft = {mode:'phrase',prompt:'a red shirt\na blue bag'};
  assert.deepEqual(evidenceLabels({frame_id:20},cameraOptions,draft),['person','wheelchair']);
});

test('cached image results do not claim zero-time model inference', () => {
  assert.deepEqual(inferencePresentation({cached:true,inference_ms:0}),{
    text:'Cached',title:'Unchanged image and settings; reusing the previous detection result.'});
  assert.deepEqual(inferencePresentation({cached:false,inference_ms:153.6,detection_engine:'grounding'}),{
    text:'154 ms',title:'Detection engine: grounding'});
  assert.deepEqual(inferencePresentation(null),{text:'—',title:''});
});
