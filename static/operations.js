import {api} from './core.js?v=network-5';
import {SpokenGuidance} from './passenger/spoken-guidance.js?v=voice-flow-1';
import {controllerAnnouncement, scenarioPresentation} from './scenario-state.js?v=standing-five-1';
import {cameraCheckPresentation} from './camera-check.js?v=cabin-mean-1';
import {mountRfidConsole} from './rfid-console.js?v=assistance-15-20';
import {AnnouncementFeed} from './announcement-feed.js?v=rfid-1';

const $ = id => document.getElementById(id);
let allowed = false, pending = false, last = null, lastOnline = false, signature = '', spoken = '';
let audioChosen = false, seedEdited = false;
let stops = {};
let rfidConsole = null, readerOrigin = location.origin;
const speaker = new SpokenGuidance();
const announcementFeed = new AnnouncementFeed();
const needNames = {ramp:'Ramp access',extra_time:'More time',priority_seat:'Priority seating',audio:'Spoken guidance',visual:'Visual guidance'};
const button = (id,text) => `<button id="${id}" type="button" class="button secondary">${text}</button>`;
const eventId = () => [...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');

export async function initOperations() {
  $('operations').innerHTML = `<div class="panel-heading"><div><p class="eyebrow">03 / CONNECTED ASSISTANCE</p><h2>One journey. Shared progress.</h2></div><span class="mini-badge">Doors & ramp simulated</span></div>
    <div class="network-bar"><div><strong>Connect a passenger device</strong><p id="networkHelp">Loading local network addresses…</p><div id="networkLinks"></div></div><div id="hostCode" hidden><span class="small-label">OPERATOR PAIRING CODE</span><strong id="pairCode"></strong><small>Passenger requests do not need this code.</small></div></div>
    <form id="pairForm" class="pair-form" hidden><label for="pairInput">Pair this operator device</label><input id="pairInput" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="6-digit code" required autocomplete="off"><button class="button">Pair device</button></form>
    <div id="operationError" class="source-error" role="alert" hidden></div>
    <section class="scenario-card" aria-labelledby="scenarioHeading">
      <div class="scenario-heading"><div><p class="eyebrow">LIVE STOP SCENARIO</p><h3 id="scenarioHeading">A stop, timed together.</h3></div><span id="scenarioPhase" class="scenario-phase" role="status">Manual demonstration</span></div>
      <div class="scenario-overview"><div class="scenario-clock"><span id="scenarioTimerLabel" class="small-label">BOARDING WINDOW</span><strong id="scenarioTimer">—</strong><span id="scenarioDetection">Inside camera always active</span></div><div class="scenario-description"><p id="scenarioDetail">Start a repeatable stop. The shared controller handles departure automatically.</p><div class="scenario-limit"><span>New taps & requests close at 50s</span><strong id="scenarioHardRemaining">—</strong></div><progress id="scenarioProgress" max="100" value="0" aria-label="Elapsed time in the 50-second admission period"></progress><div id="scenarioDepartureNotice" class="scenario-limit" hidden><span id="scenarioDepartureLabel">Planned departure</span><strong id="scenarioDepartureTimer">—</strong></div><p class="section-footnote">15s after doors open or the last outside-camera detection · 10s from an accepted RFID event · 20s for ramp access, 10s for other requests · a warning 10s before planned movement. New taps and requests stop at 50s; accepted allowances finish in full; the inside camera counts throughout the trip. An enabled inside standing check is required at every departure. After door closure, departure waits for 5 continuous seconds of fresh evidence with no standing detected, with no timeout. Doorway, emergency and mobility holds remain.</p></div></div>
      <div class="scenario-actions"><div id="manualStopChoice" class="scenario-stop-select"><label for="operationStop">Manual arrival stop</label><select id="operationStop">${Object.entries(stops).map(([id,name])=>`<option value="${id}">${name}</option>`).join('')}</select></div><button id="scenarioStart" class="button" type="button">Start stop/go scenario</button>${button('scenarioStop','Stop at next stop')}<span id="operationNextStop" class="section-footnote">Next station is set by the route.</span>${button('scenarioDisable','Return to manual')}</div>
      <div class="scenario-capacity" aria-label="Simulated seat availability"><div><span class="small-label">PRIORITY SEATS AVAILABLE</span><strong id="scenarioPriority">— <small>/ 6</small></strong><span>Anyone may sit here; offer priority when needed.</span></div><div><span class="small-label">STANDARD SEATS AVAILABLE</span><strong id="scenarioStandard">— <small>/ 20</small></strong><span>General passengers use these seats first.</span></div><div><span class="small-label">PASSENGERS ACCOUNTED FOR</span><strong id="scenarioTotal">— <small>/ 26</small></strong><span id="scenarioAdmissionRule">An available seat is required to board.</span></div></div>
      <p id="scenarioCountBasis" class="section-footnote scenario-ledger-note">Waiting for the passenger records and inside-camera check.</p>
      <div class="scenario-ledger-note" aria-label="Passenger records and camera check"><strong id="scenarioRecordedCount">Recorded passengers: —</strong><p id="scenarioCameraProgress" class="section-footnote">Camera check unavailable.</p><p class="section-footnote"><span id="scenarioCameraResult">No completed camera check.</span> <span id="scenarioCameraDifference"></span></p></div>
      <label class="inline-check"><input id="cabinCoverageInput" type="checkbox"> Inside CCTV covers the whole cabin</label><p class="section-footnote">Enable only when every passenger is visible. Every complete 30-second camera check updates the total using the rounded mean of 30 fresh counts, one per second. Boarding and alighting records change it by +1 / −1 between checks. An offline camera is unavailable, not an empty cabin. Standing checks remain live after the doors close.</p>
      <div class="scenario-events"><div><label for="scenarioSeatType">Passenger assistance preference</label><select id="scenarioSeatType"><option value="standard">General passenger</option><option value="priority">Priority assistance / senior</option></select></div><div class="scenario-event-buttons">${button('scenarioRfid','Play RFID tap')}${button('scenarioBoard','Passenger boards')}${button('scenarioAlightStandard','Alight · standard seat')}${button('scenarioAlightPriority','Alight · priority seat')}</div></div>
      <p id="scenarioAdmission" class="scenario-admission" role="status">Start the scenario to play passenger and RFID events.</p>
      <p class="section-footnote scenario-audio-note">Scenario controls enable spoken announcements on this device. Use the audio checkbox below to turn them off; every announcement also appears on screen.</p>
      <details class="scenario-seed"><summary>Set up simulated passengers</summary><form id="scenarioLoadForm"><div><label for="scenarioSeedPriority">Priority seats occupied</label><input id="scenarioSeedPriority" type="number" min="0" max="6" step="1" value="0" required></div><div><label for="scenarioSeedStandard">Standard seats occupied</label><input id="scenarioSeedStandard" type="number" min="0" max="20" step="1" value="0" required></div><div><label for="scenarioSeedRegular">General passengers in priority seats</label><input id="scenarioSeedRegular" type="number" min="0" max="6" step="1" value="0" required></div><button id="scenarioLoad" type="submit" class="button secondary">Apply simulated occupancy</button></form><p class="section-footnote">For a full-bus test, use 6 priority and 20 standard occupants. For a priority-seat offer, use 6 priority, 19 standard and 1 general passenger in priority, then play a priority RFID tap.</p></details>
    </section>
    <div class="operation-grid"><div><p class="small-label">CONTROLLER STATUS</p><p id="operationMessage" class="operation-message" role="status">Connecting to the shared controller.</p><div class="actuator-status"><span id="vehicleStop">—</span><span id="vehicleDoors">Doors —</span><span id="vehicleRamp">Ramp —</span></div><label class="inline-check"><input id="operationAudio" type="checkbox"> Read bus announcements aloud</label><p id="operationAudioStatus" class="section-footnote" role="status">Spoken guidance is off. Visual instructions are always available.</p><p class="section-footnote">CCTVs and RFID are physical inputs. The virtual bus demonstrates doors, ramp and departure; no vehicle is actuated.</p></div>
    <div><p class="small-label">MANUAL & SAFETY CONTROLS</p><div class="input-row">${button('arriveButton','Arrive at selected stop')}</div><div class="operation-actions">${button('departButton','Depart simulation')}${button('resetButton','Reset held simulation')}${button('emergencyButton','Emergency hold')}</div><label class="inline-check"><input id="obstructionInput" type="checkbox"> Simulate an obstructed doorway</label><label class="inline-check"><input id="secureInput" type="checkbox"> Confirm mobility position in simulation</label><p class="section-footnote">Camera posture cannot confirm wheelchair securement. Manual arrival and departure are unavailable while the automatic scenario is active.</p></div></div>
    <div class="operation-grid operation-lower"><div><div class="event-heading"><span class="small-label">ACTIVE REQUESTS</span><span id="requestTotal" class="event-counter">0</span></div><ol id="assistanceQueue" class="events"></ol>${button('confirmSensors','Confirm sensor-requested assistance complete')}</div>
    <div><p class="small-label">CONNECTED CARD READER</p><p class="section-footnote">Manage the four registered cards below. Your ESP32 sends each UID to this server; its saved passenger profile determines the assistance.</p></div></div><section id="rfidConsole" class="rfid-console" aria-label="RFID cards and ESP32 reader"></section>`;
  rfidConsole = mountRfidConsole($('rfidConsole'), {onTap: enableScenarioAudio, getOrigin: () => readerOrigin});
  $('pairForm').onsubmit = async event => {event.preventDefault();try{await api('/api/access/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('pairInput').value})});$('pairInput').value='';await access();}catch(error){showError(error.message);}};
  $('arriveButton').onclick = () => control('arrive',{stop_id:$('operationStop').value});
  $('departButton').onclick = () => control('depart');
  $('resetButton').onclick = () => control('reset');
  $('emergencyButton').onclick = () => control('emergency',{value:true});
  $('obstructionInput').onchange = () => control('obstruction',{value:$('obstructionInput').checked});
  $('secureInput').onchange = () => control('secure_wheelchair',{value:$('secureInput').checked});
  $('cabinCoverageInput').onchange = () => control('cabin_coverage',{value:$('cabinCoverageInput').checked});
  $('confirmSensors').onclick = () => control('confirm_sensor_requests');
  $('operationAudio').onchange = () => {
    audioChosen=true;spoken='';announcementFeed.reset();
    if(!$('operationAudio').checked) {speaker.cancel();$('operationAudioStatus').textContent='Spoken guidance is off. Visual instructions remain available.';}
    else speak();
  };
  const voiceTest = document.createElement('button');
  voiceTest.id='operationVoiceTest';voiceTest.type='button';voiceTest.className='button secondary';
  voiceTest.textContent='Test announcement voice';
  $('operationAudioStatus').after(voiceTest);
  voiceTest.onclick=async()=>{
    voiceTest.disabled=true;
    $('operationAudioStatus').textContent='Preparing a short voice test…';
    try{
      const result=await speaker.preview();
      $('operationAudioStatus').textContent=result?.played
        ? `Voice test played. ${$('operationAudio').checked?'Spoken bus announcements are on.':'Turn on Read bus announcements aloud to hear seating reminders.'}`
        : result?.reason==='playback'
          ? 'Audio playback was blocked. Allow sound for this website, then test again.'
          : 'Voice test could not play. Check the voice setup and device sound; on-screen messages remain available.';
    }catch{
      $('operationAudioStatus').textContent='Voice test could not play. Check the voice setup and device sound.';
    }finally{voiceTest.disabled=false;}
  };
  $('scenarioStart').onclick = () => {enableScenarioAudio();scenario('start');};
  $('scenarioStop').onclick = () => {enableScenarioAudio();scenario('stop');};
  $('scenarioDisable').onclick = () => scenario('disable');
  $('scenarioRfid').onclick = () => {enableScenarioAudio();scenario('rfid',{seat_type:$('scenarioSeatType').value,event_id:eventId()});};
  $('scenarioBoard').onclick = () => scenario('board',{seat_type:$('scenarioSeatType').value,event_id:eventId()});
  $('scenarioAlightStandard').onclick = () => scenario('alight',{seat_type:'standard',event_id:eventId()});
  $('scenarioAlightPriority').onclick = () => scenario('alight',{seat_type:'priority',event_id:eventId()});
  $('scenarioLoadForm').oninput = () => {seedEdited=true;};
  $('scenarioLoadForm').onsubmit = async event => {
    event.preventDefault();
    const priority=Number($('scenarioSeedPriority').value),standard=Number($('scenarioSeedStandard').value),regular=Number($('scenarioSeedRegular').value);
    if(regular>priority){showError('General passengers in priority seats cannot exceed the number of occupied priority seats.');return;}
    if(await scenario('load',{priority_occupied:priority,standard_occupied:standard,priority_regular:regular})){seedEdited=false;renderOperations(last,lastOnline);}
  };
  try {
    const config = await api('/api/assistance/config');
    stops = Object.fromEntries((config.stops || []).map(stop => [stop.id, stop.name]));
    $('operationStop').replaceChildren(...Object.entries(stops).map(([id, name]) => {
      const option = document.createElement('option'); option.value = id; option.textContent = name; return option;
    }));
  } catch { showError('Stop names are unavailable. Reconnect to the server to load the route.'); }
  await access();
}

function showError(message='') {$('operationError').textContent=message;$('operationError').hidden=!message;}
async function access(){
  try{
    const state=await api('/api/access');allowed=state.operator;
    $('pairForm').hidden=allowed;$('hostCode').hidden=!state.pairing_code;$('pairCode').textContent=state.pairing_code||'';
    $('networkHelp').textContent=state.lan_enabled?'Use the same Wi-Fi or wired network. Open a passenger link below on your phone.':'This launch is local only. Use start-lan.cmd to connect other devices.';
    $('networkLinks').replaceChildren(...state.links.map(link=>{const a=document.createElement('a');a.href=link.passenger;a.textContent=link.passenger;a.target='_blank';a.rel='noopener';return a;}));
    const link=state.links.find(x=>x.host===location.hostname)||state.links[0];
    if(link)readerOrigin = ['localhost','127.0.0.1','::1'].includes(location.hostname) ? new URL(link.detection).origin : location.origin;
    if(link){const passenger=new URL(link.passenger),bus=new URL(link.bus);passenger.hostname=location.hostname;bus.hostname=location.hostname;$('passengerLink').href=passenger.href;document.querySelector('.viewer-link').href=bus.href;}
    renderOperations(last,lastOnline);showError();
  }catch(error){showError(error.message);}
}
async function command(path,action,extra={}){
  if(pending||!allowed||!lastOnline)return false;
  pending=true;showError();renderOperations(last,lastOnline);
  try{last=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra})});return true;}
  catch(error){showError(error.message);return false;}
  finally{pending=false;renderOperations(last,lastOnline);}
}
const control=(action,extra={})=>command('/api/assistance/control',action,extra);
const scenario=(action,extra={})=>command('/api/assistance/scenario',action,extra);
function enableScenarioAudio(){
  if(!audioChosen&&!$('operationAudio').checked){$('operationAudio').checked=true;announcementFeed.reset();}
  if($('operationAudio').checked){speak();}
}
function speak(){
  const announcements=announcementFeed.take(last,lastOnline);
  if(document.hidden||!$('operationAudio')?.checked||!announcements.length)return;
  $('operationAudioStatus').textContent='Spoken guidance is on for this device. Each new announcement plays once.';
  for(const announcement of announcements)speaker.speak(announcement.message, {expiresMs:30000}).then(result=>{
    if(result?.unavailable)$('operationAudioStatus').textContent='AI audio is unavailable. Please follow the on-screen announcement.';
  }).catch(()=>{$('operationAudioStatus').textContent='AI audio is unavailable. Please follow the on-screen announcement.';});
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)speaker.cancel();else speak();});
function renderScenario(state,online,disabled){
  const view=scenarioPresentation(state,online),s=state?.scenario;
  $('scenarioPhase').textContent=view.label;$('scenarioPhase').dataset.tone=view.tone;
  $('scenarioTimer').textContent=view.timer;$('scenarioTimerLabel').textContent=view.timerLabel;
  $('scenarioDetection').textContent=view.detection;$('scenarioDetail').textContent=view.detail;
  $('scenarioHardRemaining').textContent=view.hardRemaining;$('scenarioProgress').value=view.progress;
  $('scenarioDepartureNotice').hidden=!view.departureVisible;
  $('scenarioDepartureLabel').textContent=view.departureLabel;
  $('scenarioDepartureTimer').textContent=view.departureTimer;
  for(const [id,seats,fallback] of [['scenarioPriority',view.priority,6],['scenarioStandard',view.standard,20]]){
    $(id).replaceChildren(document.createTextNode(online&&seats?`${seats.available} `:'— '),Object.assign(document.createElement('small'),{textContent:`/ ${seats?.capacity??fallback}`}));
  }
  $('scenarioTotal').replaceChildren(document.createTextNode(online&&view.total!==null?`${view.total} `:'— '),Object.assign(document.createElement('small'),{textContent:`/ ${view.capacity}`}));
  $('scenarioAdmissionRule').textContent=!online?'Seat availability unavailable.':view.full?'Bus full · new boarding is not accepted.':'An available seat is required to board.';
  $('scenarioAdmissionRule').dataset.full=String(view.full);
  $('scenarioCountBasis').textContent=online?(s?.seats?.basis||'Waiting for capacity evidence.'):'Capacity evidence unavailable while the controller is offline.';
  const cameraCheck=cameraCheckPresentation(s?.seats,online);
  $('scenarioRecordedCount').textContent=cameraCheck.recorded;
  $('scenarioCameraProgress').textContent=cameraCheck.progress;
  $('scenarioCameraProgress').dataset.status=cameraCheck.status;
  $('scenarioCameraResult').textContent=cameraCheck.result;
  $('scenarioCameraDifference').textContent=cameraCheck.difference;
  $('cabinCoverageInput').disabled=disabled;
  if(!pending)$('cabinCoverageInput').checked=Boolean(s?.seats?.count_source?.whole_cabin);
  $('scenarioStart').disabled=disabled||view.enabled||state?.vehicle?.emergency||state?.vehicle?.revalidation_required;
  $('scenarioStop').disabled=disabled||!view.travelling;
  const nextName=stops[state?.route?.next_stop_id]||'next station';
  $('scenarioStop').textContent=`Stop at ${nextName}`;
  $('operationNextStop').textContent=online?`Next: ${nextName} · A → B → C → D → E → A`:'Next station unavailable';
  $('manualStopChoice').hidden=view.enabled;
  $('scenarioDisable').disabled=disabled||!view.enabled||state?.vehicle?.motion!=='stationary'||state?.vehicle?.doors!=='closed'||state?.vehicle?.ramp!=='stowed'||state?.active_count>0;
  const activityAvailable=view.boarding&&s?.admission_open!==false&&state?.vehicle?.doors==='open';
  for(const id of ['scenarioRfid','scenarioBoard','scenarioSeatType'])$(id).disabled=disabled||!activityAvailable;
  $('scenarioAlightStandard').disabled=disabled||!activityAvailable||!view.standard?.occupied;
  $('scenarioAlightPriority').disabled=disabled||!activityAvailable||!view.priority?.occupied;
  const v=state?.vehicle;
  const preparation=!view.enabled&&v?.motion==='stationary'&&v?.doors==='closed'&&v?.ramp==='stowed'&&!v?.emergency&&!v?.revalidation_required;
  $('scenarioLoad').disabled=disabled||state?.active_count>0||!(preparation||view.boarding&&s?.admission_open!==false);
  for(const id of ['scenarioSeedPriority','scenarioSeedStandard','scenarioSeedRegular'])$(id).disabled=disabled||(view.enabled&&!view.boarding);
  if(!seedEdited){
    $('scenarioSeedPriority').value=view.priority?.occupied??0;$('scenarioSeedStandard').value=view.standard?.occupied??0;
    $('scenarioSeedRegular').value=s?.seats?.priority?.non_priority_occupied??0;
  }
  $('scenarioAdmission').textContent=!online?'Controller offline. Passenger events are unavailable.':view.admission?.message||(view.travelling?'Bus travelling. Passenger events resume at the next commanded stop.':s?.finishing_extensions?'New taps and requests are closed. Finishing the accepted allowance.':view.boarding?'Ready for a simulated RFID tap or passenger event.':'Start the scenario to play passenger and RFID events.');
  $('scenarioAdmission').dataset.denied=String(view.admission?.allowed===false);
}
export function renderOperations(state,online){
  last=state;lastOnline=online;if(!$('operationMessage'))return;
  const v=state?.vehicle,disabled=!allowed||!online||pending||!v,automatic=Boolean(state?.scenario?.enabled);
  $('operationMessage').textContent=online&&state?controllerAnnouncement(state,online)?.message||state.readiness?.message:'Controller offline. Actions and request progress are unavailable.';
  $('vehicleStop').textContent=v?`${stops[v.stop_id]||v.stop_id} · ${v.motion}`:'Stop —';
  $('vehicleDoors').textContent=`Doors · ${v?.doors||'unknown'}`;$('vehicleRamp').textContent=`Ramp · ${v?.ramp||'unknown'}`;
  for(const id of ['resetButton','emergencyButton','obstructionInput','operationStop'])$(id).disabled=disabled;
  rfidConsole?.setAccess(allowed,online,state);
  $('arriveButton').disabled=disabled||automatic;
  $('departButton').disabled=disabled||automatic||!state?.readiness?.can_depart;
  $('confirmSensors').disabled=disabled||!state?.sensor_confirmation_count;
  $('secureInput').disabled=disabled||!v?.wheelchair_aboard;
  if(!pending){$('obstructionInput').checked=Boolean(v?.obstruction);$('secureInput').checked=Boolean(v?.wheelchair_secured);}
  const queue=state?.requests||[],key=JSON.stringify(queue);$('requestTotal').textContent=String(queue.length);
  if(signature!==key){signature=key;const rows=queue.map(item=>{const li=document.createElement('li'),a=document.createElement('span'),b=document.createElement('span');a.textContent=`${item.journey==='alighting'?'Exit':'Board'} · ${stops[item.stop_id]||item.stop_id} · ${item.needs.map(n=>needNames[n]||n).join(', ')}`;b.textContent=`${item.origin} · ${item.passenger_confirmed?'Completion confirmed':item.status.replaceAll('_',' ')}`;li.append(a,b);return li;});if(!rows.length){const li=document.createElement('li');li.className='empty-event';li.textContent='No active requests. Open the passenger app to request assistance.';rows.push(li);}$('assistanceQueue').replaceChildren(...rows);}
  renderScenario(state,online,disabled);speak();
}

