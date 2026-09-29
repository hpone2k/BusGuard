import test from 'node:test';
import assert from 'node:assert/strict';
import {postureCountRows} from '../static/posture-counts.js';
import {countPresentation} from '../static/counts.js';

const estimate=(stable,raw=stable,support=.8)=>({status:'stable',stable,raw,candidate:stable,support});
const count={window_ms:1000,coverage_ms:1000,age_ms:0,stale_ms:1500,total:estimate(4),
  classes:{'standing person':estimate(1,2,.7),'sitting person':estimate(2,1,.9),'uncertain posture':estimate(1)}};
const summary={status:'observed',count_summary:count};

test('inside rows show independent observed, stable and vote support values',()=>{
  const rows=postureCountRows(summary,{status:'observed'});
  assert.deepEqual(rows.map(row=>[row.label,row.display,row.raw,row.supportText]),[
    ['standing person','1',2,'70% vote support']]);
});

test('posture rows do not change existing object classes or person totals',()=>{
  const ordinary={...count,total:estimate(4),classes:{person:estimate(4)}};
  const before=JSON.stringify(ordinary),person=countPresentation(ordinary);
  const rows=postureCountRows(summary,{status:'observed'});
  assert.equal(rows.length,1);assert.equal(person.total.stable,4);
  assert.deepEqual(Object.keys(person.classes),['person']);assert.equal(JSON.stringify(ordinary),before);
});

test('legacy sitting and uncertainty categories never become visible count rows',()=>{
  const rows=postureCountRows(summary,{status:'observed'});
  assert.deepEqual(rows.map(row=>row.label),['standing person']);
  assert.equal(postureCountRows({...summary,engine:'YOLOE'},{status:'observed',ageMs:999})[0].display,'1');
  assert.equal(postureCountRows({...summary,engine:'YOLOE'},{status:'observed',ageMs:1000})[0].display,'—');
});

test('outside table has no cabin posture rows',()=>{
  assert.deepEqual(postureCountRows(summary,{role:'outside',status:'observed'}),[]);
});

test('open doors pause posture rows without converting observations to zero',()=>{
  const rows=postureCountRows(summary,{status:'paused'});
  assert.ok(rows.every(row=>row.display==='—'&&row.raw===null&&row.supportText==='Paused · doors open'));
});

test('unavailable, offline and stale frames never show retained posture vote',()=>{
  for(const status of ['unavailable','offline','stale','disabled','draft','held']) {
    const rows=postureCountRows(summary,{status});
    assert.ok(rows.every(row=>row.display==='—'&&row.raw===null));
  }
  const rows=postureCountRows(summary,{status:'observed',ageMs:1500});
  assert.ok(rows.every(row=>row.display==='—'&&row.raw===null&&row.supportText==='Stale'));
});

test('warmup and image snapshots never claim temporal vote certainty',()=>{
  const warming={...summary,count_summary:{...count,coverage_ms:400}};
  assert.ok(postureCountRows(warming,{status:'observed'}).every(row=>row.display==='—'&&row.supportText==='Warming up'));
  const image=postureCountRows(summary,{status:'observed',mode:'image'});
  assert.equal(image[0].display,'2');assert.equal(image[0].supportText,'Single image');
  assert.ok(postureCountRows({status:'observed'},{status:'observed'}).every(row=>row.raw===null&&row.display==='—'));
});

test('semantic posture votes retain true inference age independently of generic count freshness',()=>{
  const semantic={...summary,engine:'LocateAnything',age_ms:1300};
  assert.equal(postureCountRows(semantic,{status:'observed',ageMs:1400})[0].display,'1');
  assert.equal(postureCountRows(semantic,{status:'observed',ageMs:3999})[0].display,'1');
  const stale=postureCountRows(semantic,{status:'observed',ageMs:4000});
  assert.ok(stale.every(row=>row.display==='—'&&row.raw===null&&row.supportText==='Stale'));
  assert.ok(postureCountRows({...semantic,engine:undefined},{status:'observed'}).every(row=>row.display==='—'));
  assert.equal(countPresentation(count,{ageMs:1500}).status,'stale');
  assert.equal(count.stale_ms,1500);
});
