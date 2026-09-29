import test from 'node:test';
import assert from 'node:assert/strict';
import {standingConfidence,standingSettings} from '../static/standing-settings.js';

test('standing score defaults independently of generic person confidence',()=>{
  const generic={confidence:.35,class_confidences:{person:.65,wheelchair:.4}};
  const options={...generic,...standingSettings({role:'inside',mode:'objects',prompt:'person',checked:true})};
  assert.equal(options.standing_confidence,.10);assert.equal(options.posture_enabled,true);
  assert.equal(options.confidence,.35);assert.deepEqual(options.class_confidences,generic.class_confidences);
  assert.equal(Object.hasOwn(generic,'standing_confidence'),false);
});

test('standing slider values survive restore and serialization within backend bounds',()=>{
  for(const score of [.05,.10,.17,.95]) {
    const restored=standingConfidence(score);
    const options=standingSettings({role:'inside',mode:'objects',prompt:'person',checked:true,confidence:restored.toFixed(2)});
    assert.equal(JSON.parse(JSON.stringify(options)).standing_confidence,score);
  }
  for(const invalid of [null,true,'',NaN,Infinity,-1,.049,.951,'oops'])
    assert.throws(()=>standingConfidence(invalid),/between 0.05 and 0.95/);
});

test('standing sensitivity cannot enable outside, phrase or unchecked inference',()=>{
  for(const patch of [{role:'outside'},{mode:'phrase'},{prompt:'person in a red shirt'},{checked:false}]) {
    const options=standingSettings({role:'inside',mode:'objects',prompt:'person',checked:true,confidence:.07,...patch});
    assert.equal(options.posture_enabled,false);assert.equal(options.standing_confidence,.07);
  }
});
