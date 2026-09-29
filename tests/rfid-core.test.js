import test from 'node:test';
import assert from 'node:assert/strict';
import {cardProfile, cardTapMessage, readerConfig, rfidAdmission} from '../static/rfid-core.js';

test('normal, senior and pregnant use explicit presets; custom flags stay independent', () => {
  assert.deepEqual(cardProfile('normal', '', true, true), {passenger_type:'normal',custom_label:'',priority:false,extra_time:false});
  for (const type of ['senior', 'pregnant']) {
    assert.equal(cardProfile(type).priority, true); assert.equal(cardProfile(type).extra_time, true);
  }
  assert.deepEqual(cardProfile('custom', 'Walking aid', true, false),
    {passenger_type:'custom',custom_label:'Walking aid',priority:true,extra_time:false});
});
test('custom profile labels are validated before submission', () => {
  for (const label of ['', '<script>', 'a'.repeat(61), 'bad\nlabel']) assert.throws(() => cardProfile('custom', label));
  assert.throws(() => cardProfile('unknown'));
});
test('reader setup only uses the dedicated token and detection origin', () => {
  assert.equal(readerConfig({readerId:'busguard-esp32',token:'x'.repeat(32),origin:'http://192.168.1.59:4479/'}),
    `Server: http://192.168.1.59:4479\nReader ID: busguard-esp32\nReader key: ${'x'.repeat(32)}`);
  assert.throws(() => readerConfig({readerId:'bad"id',token:'x'.repeat(32),origin:'http://localhost:4479'}));
});
test('tap status preserves rejected admission feedback', () => {
  assert.match(cardTapMessage(null), /Ready/);
  assert.equal(cardTapMessage({card_id:'2',message:'No seats available.',allowed:false}), 'Card 2 · No seats available.');
});

test('RFID controls require a fresh open-door boarding window, including for alighting', () => {
  const open = {vehicle: {motion:'stationary', doors:'open'}, scenario: {enabled:true, admission_open:true}};
  assert.equal(rfidAdmission(open).accepting, true);
  for (const doors of ['opening', 'closing', 'closed', 'unknown'])
    assert.equal(rfidAdmission({...open,vehicle:{...open.vehicle,doors}}).accepting, false);
  for (const motion of ['braking', 'moving', 'unknown'])
    assert.equal(rfidAdmission({...open,vehicle:{...open.vehicle,motion}}).accepting, false);
  for (const flag of ['emergency','revalidation_required'])
    assert.equal(rfidAdmission({...open,vehicle:{...open.vehicle,[flag]:true}}).accepting, false);
  assert.equal(rfidAdmission(open, false).accepting, false);
  assert.equal(rfidAdmission(null).accepting, false);
});

test('open doors after admission ends cannot unlock taps; full bus still permits exit scans', () => {
  const open = {vehicle:{motion:'stationary',doors:'open'},scenario:{enabled:true,admission_open:false,phase:'boarding'}};
  assert.equal(rfidAdmission(open).accepting, false);
  assert.equal(rfidAdmission({...open,scenario:{enabled:false,admission_open:true}}).accepting, false);
  assert.equal(rfidAdmission({...open,scenario:{enabled:true,admission_open:true,seats:{available:0}}}).accepting, true);
});
