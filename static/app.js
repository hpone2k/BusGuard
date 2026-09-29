import {detectionPausedFor, postureVisible} from './journey-detection.js?v=door-preview-1';
import {ConfidenceProfiles, PromptDrafts, ResolutionProfiles, AnalyzedFrame, captureDimensions, detectionScoreLabel, abortable, loadVideo, evidenceLabels, inferencePresentation, validatePrompt, api, contentRect, colorFor, liveBoxes} from './core.js?v=door-preview-1';
import {countPresentation} from './counts.js';
import {SourceLease, PreviewLease, agedSource, matchingSource, sourceEvidence, shouldPreview, browserPreviewMode, backgroundCameraActive} from './console-state.js?v=live-camera-3';
import {postureAllowed, drawPosture, postureFrameFresh} from './posture.js?v=standing-only-1';
import {posturePresentation} from './posture-status.js?v=standing-five-1';
import {departureConfirmationMs} from './passenger/departure-notice.js?v=standing-five-1';
import {standingConfidence, standingSettings} from './standing-settings.js?v=standing-threshold-1';
import {postureCountRows} from './posture-counts.js?v=standing-only-1';
import {initOperations, renderOperations} from './operations.js?v=standing-five-1';

const $ = id => document.getElementById(id);
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function detectionPaused(role) {
  return detectionPausedFor(role, app.snapshot?.assistance, app.online, performance.now() - app.received);
}
const labels = {rtsp:'RTSP CCTV', webcam:'Camera', image:'Image', video:'Video'};
const kinds = ['rtsp','webcam','image','video'];
const phraseGuide = 'Write one target per line, or separate targets with semicolons. Up to 8 short, visually specific phrases. Thresholds measure model match strength, not the probability a detection is correct. One object can match multiple phrases; counts overlap.';
const help = {
  rtsp:'Use the same RTSP address that works in VLC. Credentials stay in server memory.',
  webcam:'Choose a connected camera. Your browser will request camera permission.',
  image:'A single image is a test input. Temporal voting does not apply.',
  video:'Recorded footage is analysed during playback and stays labelled as a test.'
};
const app = {ready:false, backend:null, online:false, snapshot:null, received:0, role:'inside', booted:false};

function sourceHTML(role, index) {
  const heading = role === 'outside' ? 'Outside · Boarding area' : 'Inside · Passenger cabin';
  return `<article class="source-card" id="card-${role}" aria-label="${heading}">
  <header class="source-heading"><div class="source-heading-title"><span class="role-number">0${index + 1}</span><div><h2>${heading}</h2><p>${role === 'outside' ? 'Arrival & accessibility needs' : 'Occupancy & standing alerts'}</p></div></div><span class="state-pill" data-el="pill">Not connected</span></header>
  <div class="source-tabs" role="group" aria-label="${role} source type">${kinds.map(kind => `<button data-kind="${kind}" aria-pressed="${kind === 'rtsp'}">${icon(kind === 'rtsp' ? 'camera' : kind)}${labels[kind]}</button>`).join('')}</div>
  <div class="stage" data-el="stage"><span class="stage-tag" data-el="tag"><i></i> ${role.toUpperCase()} / NO INPUT</span><span class="stage-time" data-el="frame">—</span>
    <div class="placeholder" data-el="placeholder"><div class="viewfinder">${icon('camera')}</div><h3 data-el="empty-title">A new perspective starts here.</h3><p data-el="empty-copy">Connect your CCTV or choose a camera, image or video to begin.</p></div>
    <img data-el="image" alt="${role} source preview with detection overlay" hidden><video data-el="video" muted playsinline hidden aria-label="${role} video preview"></video><canvas data-el="overlay" aria-hidden="true"></canvas>
  </div>
  <div class="preview-choice" data-el="preview-choice" hidden><label for="${role}-preview-view">Preview</label><select id="${role}-preview-view" data-el="preview-view" aria-describedby="${role}-preview-detail"><option value="live">Live video</option><option value="detection">Detection frame</option></select><p id="${role}-preview-detail" data-el="preview-detail">Live video stays smooth. Choose Detection frame to see boxes on the image that was analysed.</p></div>
  <div class="source-metrics"><div><span class="small-label">STABLE OBJECTS</span><strong data-el="count">—</strong></div><div><span class="small-label">MODEL INFERENCE</span><strong data-el="inference">—</strong></div><div><span class="small-label">OBSERVATION AGE</span><strong data-el="age">—</strong></div></div>
  <div class="source-controls">
    <div data-input="rtsp"><label for="${role}-url">RTSP stream address</label><div class="input-row"><div class="input-wrap"><input id="${role}-url" data-el="url" type="password" autocomplete="off" spellcheck="false" placeholder="rtsp://user:password@camera:554/stream" aria-label="${role} RTSP address"><button data-action="show-url" aria-label="Show ${role} RTSP address">Show</button></div><button class="button" data-action="connect">Connect</button></div></div>
    <div data-input="webcam" hidden><label for="${role}-device">Connected camera</label><div class="input-row"><select id="${role}-device" data-el="device" aria-label="${role} camera device"><option value="">Default camera</option></select><button class="button" data-action="camera">Open camera</button></div></div>
    <div data-input="image" hidden><label>Image source</label><div class="file-row"><button class="button secondary" data-action="image-file">${icon('image')} Choose image</button><button class="button secondary" data-action="sample">Try sample image</button><span class="file-name" data-el="image-name">No image selected</span></div><input data-el="image-file" type="file" accept="image/jpeg,image/png,image/webp,image/bmp" hidden aria-label="${role} image file"></div>
    <div data-input="video" hidden><label>Video source</label><div class="file-row"><button class="button secondary" data-action="video-file">${icon('video')} Choose video</button><span class="file-name" data-el="video-name">No video selected</span></div><input data-el="video-file" type="file" accept="video/*,.mkv,.avi,.m4v" hidden aria-label="${role} video file"><div class="input-row" data-el="playback" hidden><button class="button secondary" data-el="play-pause">Pause playback</button><input type="range" min="0" max="0" step="0.1" value="0" data-el="seek" aria-label="${role} video playback position"><span class="file-name" data-el="playback-time">0:00</span></div></div>
    <p class="source-help" data-el="help">${help.rtsp}</p><div class="source-error" data-el="error" role="alert" hidden></div>
    <div class="camera-status"><span class="source-status" data-el="status">Ready to connect</span><button class="button stop" data-action="stop" disabled>Stop source</button></div>
  </div>
  <section class="posture-option posture-monitor" data-el="posture-option" ${role !== 'inside' ? 'hidden' : ''} aria-label="Inside camera posture checks">
    <div class="posture-monitor-controls"><label class="inline-check" for="${role}-posture"><input id="${role}-posture" type="checkbox" data-el="posture" ${role === 'inside' ? 'checked' : ''} aria-describedby="${role}-posture-detail"><span data-el="posture-label">Standing detection · YOLOE</span></label><button class="button secondary" type="button" data-action="apply-posture">Apply setting</button></div>
    <div class="standing-threshold"><div><label for="${role}-standing-confidence">Standing threshold</label><output data-el="standing-confidence-value" for="${role}-standing-confidence">0.10</output></div><input id="${role}-standing-confidence" data-el="standing-confidence" type="range" min="0.05" max="0.95" step="0.01" value="0.10" aria-describedby="${role}-standing-help"><p id="${role}-standing-help">Pretrained prompt · tune with your camera; score is not an accuracy probability.</p></div>
    <strong class="posture-monitor-title" data-el="posture-title" role="status">Checking inside camera status…</strong><p id="${role}-posture-detail" class="section-footnote" data-el="posture-detail">Connect the inside camera. Standing detection begins after the doors close.</p>
    <div class="posture-legend" data-el="posture-legend" hidden><span><i data-posture="standing"></i>Standing person</span></div>
    <p class="posture-audio-link"><a href="#operationAudio">Enable bus announcements or test the voice below ↓</a></p>
  </section>
  <details class="detector-settings"><summary>${icon('sliders')} Detection settings <span data-el="settings-summary">6 targets · 640 px</span></summary><div class="settings-inner">
    <div class="settings-grid"><div class="setting-field"><label for="${role}-mode">Target mode</label><select id="${role}-mode" data-el="mode"><option value="objects">Object categories</option><option value="phrase">Detailed phrases</option></select></div><div class="setting-field"><label for="${role}-size">Detection resolution</label><select id="${role}-size" data-el="size"><option value="384">384 · Fastest</option><option value="512">512 · Fast</option><option value="640" selected>640 · Balanced</option><option value="960">960 · More detail</option></select></div></div>
    <div class="setting-field"><label for="${role}-prompt" data-el="prompt-label">Objects, separated by commas</label><textarea id="${role}-prompt" data-el="prompt" maxlength="2048" rows="2" spellcheck="false" aria-describedby="${role}-phrase-help">person, wheelchair, stroller, walker, crutch, cane</textarea><p id="${role}-phrase-help" class="phrase-help" data-el="phrase-help" hidden>${phraseGuide}</p></div>
    <div class="settings-grid"><div class="setting-field"><label for="${role}-stability">Box stability</label><select id="${role}-stability" data-el="stabilization"><option value="balanced">Balanced tracking</option><option value="responsive">Responsive tracking</option><option value="off">Raw detections</option></select></div><div class="setting-field"><label for="${role}-motion">Camera movement</label><select id="${role}-motion" data-el="motion"><option value="off">Fixed camera</option><option value="on">Moving camera</option></select></div></div>
    <p class="confidence-heading" data-el="confidence-heading">Confidence per target</p><div class="confidence-list" data-el="confidences"></div><div class="settings-apply"><button class="button secondary" data-action="apply">Apply settings</button><span data-el="settings-hint">Settings are independent for each source.</span></div>
  </div></details></article>`;
}

class InputCard {
  constructor(role, index) {
    this.role = role; $('sources').insertAdjacentHTML('beforeend', sourceHTML(role,index)); this.root = $(`card-${role}`);
    this.el = Object.fromEntries([...this.root.querySelectorAll('[data-el]')].map(node => [node.dataset.el,node]));
    this.lease = new SourceLease(); this.previewLease = new PreviewLease(); this.confidenceProfiles = new ConfidenceProfiles(); this.confidences = this.confidenceProfiles.forMode('objects');
    this.promptDrafts = new PromptDrafts(this.el.prompt.value);
    this.resolutions = new ResolutionProfiles(); this.analyzed = new AnalyzedFrame(); this.sessionOptions = null;
    this.kind = 'rtsp'; this.tail = Promise.resolve(); this.active = false; this.session = null; this.url = null; this.camera = null;
    this.frameId = 0; this.result = null; this.captureAt = 0; this.previewId = null; this.previewPostureAllowed = null; this.previewURL = null; this.previewBusy = false;
    this.pending = false; this.touched = false; this.hasMedia = false; this.seeking = false; this.lastMediaTime = -1; this.sampleBusy = false;
    this.processed = document.createElement('canvas'); this.processedCtx = this.processed.getContext('2d');
    this.root.querySelectorAll('[data-kind]').forEach(button => {button.onclick = () => this.select(button.dataset.kind);});
    this.root.querySelector('[data-action=connect]').onclick = () => this.connect();
    this.root.querySelector('[data-action=camera]').onclick = () => this.openCamera();
    this.root.querySelector('[data-action=stop]').onclick = () => this.stop();
    this.root.querySelector('[data-action=apply]').onclick = () => this.apply();
    this.root.querySelector('[data-action=apply-posture]').onclick = () => this.apply();
    this.el.posture.onchange = () => {this.standingControls();this.renderPosture(performance.now());};
    this.el['standing-confidence'].oninput = () => {this.standingControls();this.renderPosture(performance.now());};
    this.el['preview-view'].onchange = () => {this.controls();this.render(performance.now());};
    this.root.querySelector('[data-action=sample]').onclick = () => this.openSample();
    this.el['play-pause'].onclick = () => {if(this.pending || !this.hasMedia) return;if(this.el.video.paused) this.el.video.play().catch(error => this.error(error.message)); else this.el.video.pause();};
    this.el.seek.oninput = () => {if(!this.pending && this.hasMedia) this.el.video.currentTime = Number(this.el.seek.value);};
    this.root.querySelector('[data-action=show-url]').onclick = event => {const show = this.el.url.type === 'password'; this.el.url.type = show ? 'text' : 'password'; event.currentTarget.textContent = show ? 'Hide' : 'Show'; event.currentTarget.setAttribute('aria-label',`${show ? 'Hide' : 'Show'} ${role} RTSP address`);};
    for (const kind of ['image','video']) {
      this.root.querySelector(`[data-action=${kind}-file]`).onclick = () => this.el[`${kind}-file`].click();
      this.el[`${kind}-file`].onchange = event => {const file = event.target.files[0]; if (file) this.openFile(kind,file); event.target.value = '';};
    }
    this.el.device.onchange = () => {if (this.kind === 'webcam' && this.active) this.openCamera();};
    this.el.prompt.oninput = () => this.renderConfidence();
    this.el.mode.onchange = () => {this.el.prompt.value = this.promptDrafts.switch(this.el.mode.value,this.el.prompt.value); this.el.size.value = this.resolutions.switch(this.el.mode.value,this.el.size.value); this.confidences = this.confidenceProfiles.forMode(this.el.mode.value); this.renderConfidence();};
    this.el.size.onchange = () => this.renderConfidence();
    this.el.video.addEventListener('seeking', () => {if(this.kind === 'video' && this.hasMedia && !this.pending && !this.seeking){this.seeking = true; this.result = null; this.restartVideo();}});
    this.el.video.addEventListener('seeked', () => {this.seeking = false;});
    this.el.video.addEventListener('ended', () => {if(this.kind === 'video' && this.hasMedia && !this.pending && this.el.video.ended) this.stop(false);});
    this.el.video.addEventListener('play', () => {if(this.kind === 'video' && !this.active && !this.pending && this.hasMedia) this.startMedia();});
    this.renderConfidence(); this.devices();
  }
  error(message = '') {this.el.error.textContent = message; this.el.error.hidden = !message;}
  status(message) {this.el.status.textContent = message;}
  options() {validatePrompt(this.el.prompt.value,this.el.mode.value);return {prompt:this.el.prompt.value.trim(), mode:this.el.mode.value, size:Number(this.el.size.value), confidence:this.confidences.fallback, class_confidences:this.confidences.toJSON(), stabilization:this.el.stabilization.value, camera_motion:this.el.motion.value === 'on', ...standingSettings({role:this.role,mode:this.el.mode.value,prompt:this.el.prompt.value,checked:this.el.posture.checked,confidence:this.el['standing-confidence'].value})};}
  restore(options) {if (!options) return; for(const key of ['prompt','mode','size','stabilization']) this.el[key].value = options[key]; this.el.posture.checked=Boolean(options.posture_enabled); this.el['standing-confidence'].value=standingConfidence(options.standing_confidence); this.promptDrafts.restore(options.mode,options.prompt); this.resolutions.restore(options.mode,options.size); this.el.motion.value = options.camera_motion ? 'on' : 'off'; this.confidences = this.confidenceProfiles.forMode(options.mode); this.confidences.restore(options.prompt, options.mode, options.class_confidences, options.confidence); this.renderConfidence();}
  previewMode() {return browserPreviewMode(this.kind,this.sessionOptions,this.el['preview-view'].value);}
  alignedPreview() {return this.previewMode().aligned;}
  clearAnalyzed(token = this.lease.generation) {this.analyzed.reset(token);this.processed.width = 0;this.processed.height = 0;this.el.overlay.getContext('2d').clearRect(0,0,this.el.overlay.width,this.el.overlay.height);}
  renderConfidence() {
    this.el.posture.disabled=!postureAllowed(this.role,this.el.mode.value,this.el.prompt.value);
    this.root.querySelector('[data-action=apply-posture]').disabled = this.pending || !app.ready || this.el.posture.disabled;
    this.standingControls();
    const entries = this.confidences.sync(this.el.prompt.value,this.el.mode.value); this.el.confidences.replaceChildren();
    for(const [index,entry] of entries.entries()) {
      const row = document.createElement('div'); row.className = 'confidence-row'; const heading = document.createElement('div');
      const label = document.createElement('label'); const input = document.createElement('input'); const output = document.createElement('output');
      input.id = `${this.role}-confidence-${index}`; input.type = 'range'; input.min = 5; input.max = 95; input.value = Math.round(entry.value * 100); label.htmlFor = input.id; label.textContent = entry.label; label.title = entry.label; output.htmlFor = input.id;
      input.setAttribute('aria-label',`${this.role} ${entry.label} ${this.el.mode.value === 'phrase' ? 'match threshold' : 'confidence'}`); input.oninput = () => {this.confidences.set(entry.label, Number(input.value)/100); output.textContent = this.el.mode.value === 'phrase' ? (Number(input.value)/100).toFixed(2) : `${input.value}%`;}; input.oninput();
      heading.append(label,output); row.append(heading,input); this.el.confidences.append(row);
    }
    const phrase = this.el.mode.value === 'phrase';
    this.el['confidence-heading'].textContent = phrase ? 'Match threshold per phrase' : 'Confidence per target';
    this.el['phrase-help'].hidden = !phrase; this.el['prompt-label'].textContent = phrase ? 'Detailed phrases · one target per line' : 'Objects, separated by commas';
    this.el.prompt.rows = phrase ? 4 : 2;
    this.el.prompt.placeholder = phrase ? 'a person with a red shirt\na person carrying a backpack\na person using a wheelchair' : 'person, wheelchair, stroller';
    try {validatePrompt(this.el.prompt.value,this.el.mode.value);this.el.prompt.setCustomValidity('');this.el['settings-hint'].textContent = phrase ? `${entries.length} / 8 phrases · indoor phrase subsets cannot verify that no passenger is standing` : 'Settings are independent for each source.';}
    catch(error) {this.el.prompt.setCustomValidity(error.message);this.el['settings-hint'].textContent = error.message;}
    this.el['settings-summary'].textContent = `${entries.length} target${entries.length === 1 ? '' : 's'} · ${this.el.size.value} px`;
  }
  standingControls() {
    const input=this.el['standing-confidence'],score=standingConfidence(input.value).toFixed(2);
    input.disabled=this.pending || !this.el.posture.checked || !postureAllowed(this.role,this.el.mode.value,this.el.prompt.value);
    input.setAttribute('aria-valuetext',`${score} model match score`);
    this.el['standing-confidence-value'].value=score;
  }
  controls() {
    this.standingControls();
    this.root.querySelectorAll('[data-kind]').forEach(button => button.setAttribute('aria-pressed', button.dataset.kind === this.kind));
    this.root.querySelectorAll('[data-input]').forEach(node => {node.hidden = node.dataset.input !== this.kind;});
    this.el.help.textContent = help[this.kind]; this.root.querySelector('[data-action=stop]').disabled = !this.active && !this.pending && !this.hasMedia && !this.camera?.enabled;
    this.root.querySelector('[data-action=connect]').disabled = this.pending || !app.ready;
    this.root.querySelector('[data-action=camera]').disabled = this.pending || !app.ready;
    this.root.querySelector('[data-action=apply]').disabled = this.pending || !app.ready;
    this.root.querySelector('[data-action=apply-posture]').disabled = this.pending || !app.ready || !postureAllowed(this.role,this.el.mode.value,this.el.prompt.value);
    this.root.querySelector('[data-action=sample]').disabled = this.pending || this.sampleBusy || !app.ready;
    this.el.tag.lastChild.textContent = ` ${this.role.toUpperCase()} / ${this.kind === 'rtsp' ? 'CCTV' : this.kind === 'webcam' ? 'CAMERA' : this.kind === 'image' ? 'IMAGE TEST' : 'RECORDED VIDEO'}${this.alignedPreview() ? ' · ANALYSED FRAMES' : ''}`;
    this.el['preview-choice'].hidden = !this.previewMode().selectable;
    this.el['preview-detail'].textContent = this.alignedPreview() ? 'Sampled image with matching detection boxes. Choose Live video for continuous camera movement.' : 'Live video stays smooth. Choose Detection frame to see boxes on the image that was analysed.';
    this.el.playback.hidden = !(this.kind === 'video' && this.hasMedia && this.alignedPreview());
    this.el['play-pause'].disabled = this.pending;this.el.seek.disabled = this.pending;
  }
  operation(action) {
    const token = this.lease.renew(); this.touched = true; this.pending = true; this.active = false; this.error(); this.controls();
    this.clearAnalyzed(token); this.result = null;
    // A source switch revokes the old evidence immediately in this console,
    // before asynchronous server/session cleanup can finish.
    if(app.snapshot?.sources?.[this.role]) app.snapshot.sources[this.role] = {...app.snapshot.sources[this.role],connected:false,status:'disconnected'};
    this.tail = this.tail.catch(() => {}).then(async () => {
      if(!this.lease.current(token)) return;
      try {await action(token);} catch(error) {if(this.lease.current(token) && error.name !== 'AbortError'){this.error(error.message); this.status('Source needs attention');}}
      finally {if(this.lease.current(token)){this.pending = false; this.controls();}}
    });
    return this.tail;
  }
  async deleteSession() {const id = this.session; this.session = null; if(id) await api(`/api/sessions/${id}`,{method:'DELETE'}).catch(() => {});}
  async teardown({media = true, camera = true} = {}) {
    await this.deleteSession();
    if(camera && this.camera?.enabled) {await api(`/api/bus/cameras/${this.role}`,{method:'DELETE'}); this.camera = null;}
    if(camera) {const foreign = app.snapshot?.sources?.[this.role]; if(foreign?.session_id && foreign.connected) await api(`/api/sessions/${foreign.session_id}`,{method:'DELETE'}).catch(() => {});}
    this.result = null; this.previewId = null; this.lastMediaTime = -1; this.clearAnalyzed();
    if(this.previewURL){URL.revokeObjectURL(this.previewURL);this.previewURL = null;}
    if(media) {this.hasMedia = false;this.seeking = false;this.sessionOptions = null;this.lease.releaseStream(); this.el.video.pause(); this.el.video.srcObject = null; this.el.video.removeAttribute('src'); this.el.video.load(); this.el.image.removeAttribute('src'); if(this.url) URL.revokeObjectURL(this.url); this.url = null; this.el.image.hidden = true; this.el.video.hidden = true; this.el.placeholder.hidden = false;this.el['image-name'].textContent = 'No image selected';this.el['video-name'].textContent = 'No video selected';}
  }
  select(kind) {if(kind === this.kind) return; return this.operation(async token => {await this.teardown(); if(!this.lease.current(token)) return; this.kind = kind; this.status('Ready to connect'); this.el['empty-title'].textContent = kind === 'image' ? 'Bring a scene into focus.' : kind === 'video' ? 'Explore a recorded journey.' : 'A new perspective starts here.'; this.el['empty-copy'].textContent = kind === 'image' ? 'Choose an image to test passenger and object recognition.' : kind === 'video' ? 'Choose a video to analyse its frames as it plays.' : 'Connect this source to begin real-time observations.'; this.controls();});}
  stop(clear = true) {return this.operation(async token => {await this.teardown({media:clear}); if(this.lease.current(token)){this.el.video.pause(); this.status(clear ? 'Source stopped' : 'Playback ended · press play to analyse again');}});}
  async devices() {
    if(!navigator.mediaDevices?.enumerateDevices) return;
    try {const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'videoinput'); const selected = this.el.device.value; this.el.device.replaceChildren(new Option('Default camera','')); devices.forEach((device,index) => this.el.device.add(new Option(device.label || `Camera ${index + 1}`,device.deviceId))); if([...this.el.device.options].some(option => option.value === selected)) this.el.device.value = selected;} catch {}
  }
  connect() {
    let options; try {options = this.options();} catch(error) {this.error(error.message);return;}
    const url = this.el.url.value.trim().replace(/^rtsp\\:\/\//i,'rtsp://');
    if(!/^rtsps?:\/\//i.test(url)){this.error('Enter a valid RTSP address, starting with rtsp:// or rtsps://.');return;}
    return this.operation(async token => {await this.teardown(); if(!this.lease.current(token)) return; this.status('Connecting to CCTV…'); const camera = await api(`/api/bus/cameras/${this.role}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,name:`${this.role === 'inside' ? 'Inside' : 'Outside'} CCTV`,options})}); this.camera = camera; if(!this.lease.current(token)) return; this.active = true; this.status('Waiting for the first camera frame…');});
  }
  openCamera() {return this.operation(async token => {
    await this.teardown(); if(!this.lease.current(token)) return;
    if(!navigator.mediaDevices?.getUserMedia) throw new Error('Camera access needs localhost or HTTPS and browser permission.');
    this.status('Waiting for camera permission…'); const device = this.el.device.value;
    const stream = await navigator.mediaDevices.getUserMedia({audio:false,video:{width:{ideal:1280},height:{ideal:720},frameRate:{ideal:24},...(device ? {deviceId:{exact:device}} : {})}});
    if(!this.lease.acceptStream(token,stream)) return; this.el.video.srcObject = stream; this.el.video.controls = false; this.el.video.hidden = false; this.el.placeholder.hidden = true; this.hasMedia = true; await this.el.video.play();
    this.devices(); await this.begin(token);
  });}
  async openSample() {
    if(this.sampleBusy || this.pending || this.kind !== 'image') return;
    const token = this.lease.generation;this.sampleBusy = true;this.controls();
    try {const response = await fetch('/static/sample.jpg',{signal:this.lease.abort.signal});if(!response.ok) throw new Error('Unable to load the sample image.');const blob = await response.blob();if(this.lease.current(token) && this.kind === 'image') await this.openFile('image',new File([blob],'Sample street scene.jpg',{type:'image/jpeg'}));}
    catch(error) {if(this.lease.current(token) && error.name !== 'AbortError') this.error(error.message);}
    finally {this.sampleBusy = false;this.controls();}
  }
  openFile(kind,file) {return this.operation(async token => {
    await this.teardown(); if(!this.lease.current(token)) return; this.kind = kind; this.controls(); this.url = URL.createObjectURL(file); this.el[`${kind}-name`].textContent = file.name;
    if(kind === 'image') {this.el.image.src = this.url; await abortable(this.el.image.decode(),this.lease.abort.signal);if(!this.lease.current(token)) return;this.el.image.hidden = false;}
    else {this.el.video.src = this.url; this.el.video.controls = true; this.el.video.hidden = false; await loadVideo(this.el.video,this.lease.abort.signal);}
    if(!this.lease.current(token)) return; this.el.placeholder.hidden = true; this.hasMedia = true;
    if(kind === 'video') await this.el.video.play(); await this.begin(token);
  });}
  startMedia() {return this.operation(async token => {await this.teardown({media:false,camera:false}); if(this.lease.current(token)) await this.begin(token);});}
  restartVideo() {if(!this.hasMedia) return; return this.operation(async token => {await this.teardown({media:false,camera:false}); if(this.lease.current(token)) await this.begin(token);});}
  apply() {
    try {this.options();} catch(error) {this.error(error.message);return;}
    if(this.kind === 'rtsp') {if(this.camera?.enabled && !this.el.url.value){this.error('Enter the RTSP address again to reconnect with these settings. The server does not return stored credentials.');return;} if(this.camera?.enabled || this.el.url.value) return this.connect();}
    else if(this.hasMedia) return this.startMedia();
    this.status('Settings ready for the next source');
  }
  async begin(token) {
    if(!this.lease.current(token)) return;
    const options = this.options();
    const session = await api('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({options,kind:this.kind === 'image' ? 'image' : this.kind === 'video' ? 'video' : 'live',source_role:this.role,source_name:`${this.role === 'inside' ? 'Inside' : 'Outside'} · ${labels[this.kind]}${this.kind === 'image' || this.kind === 'video' ? ' test' : ''}`})});
    if(!this.lease.current(token)){await api(`/api/sessions/${session.id}`,{method:'DELETE'}).catch(() => {});return;}
    this.session = session.id; this.sessionOptions = options; this.active = true; this.frameId = 0; this.controls();this.status(this.kind === 'image' ? 'Analysing image…' : this.alignedPreview() ? 'Analysed frames · boxes stay aligned with their captured image' : 'Detection is running'); this.loop(token,session.id);
  }
  async loop(token,session) {
    // An old asynchronous JPEG encoder must never share pixels with a new
    // source/session. Reuse one canvas only within this loop's generation.
    const capture = document.createElement('canvas'), captureContext = capture.getContext('2d');
    try {
      while(this.lease.current(token) && this.active) {
        if(document.hidden && !backgroundCameraActive(this)){await wait(200);continue;}
        if(detectionPaused(this.role)) {await wait(200);continue;}
        const media = this.kind === 'image' ? this.el.image : this.el.video;
        if(this.kind === 'video' && (media.paused || media.seeking || media.readyState < 2 || media.currentTime === this.lastMediaTime)){await wait(80);continue;}
        const width = media.videoWidth || media.naturalWidth, height = media.videoHeight || media.naturalHeight;
        if(!width || !height){await wait(80);continue;}
        const dimensions = captureDimensions(width,height,this.sessionOptions.size,this.sessionOptions.mode); capture.width = dimensions.width; capture.height = dimensions.height; captureContext.drawImage(media,0,0,capture.width,capture.height);
        const timestamp = performance.now(); const mediaTime = media.currentTime; const blob = await new Promise(resolve => capture.toBlob(resolve,'image/jpeg',.86)); if(!blob) throw new Error('Unable to capture this source.');
        if(!this.lease.current(token)) break;
        let result;
        try {result = await api(`/api/sessions/${session}/frames?frame_id=${++this.frameId}&captured_at=${timestamp}`,{method:'POST',headers:{'Content-Type':'image/jpeg'},body:blob,signal:this.lease.abort.signal});}
        catch(error){if(error.status === 423 || error.status === 429 && this.kind !== 'image'){await wait(200);continue;}throw error;}
        if(!this.lease.current(token) || this.session !== session) break;
        if(detectionPaused(this.role)) continue;
        this.result = result; this.captureAt = timestamp; this.lastMediaTime = mediaTime;
        if(this.previewMode().retainFrame && this.analyzed.accept({capturedAt:timestamp,receivedAt:performance.now(),frameId:result.frame_id,detections:result.detections,seating:result.seating_summary,width:capture.width,height:capture.height},token,performance.now())) {
          this.processed.width = capture.width;this.processed.height = capture.height;this.processedCtx.drawImage(capture,0,0);
        }
        this.status(this.kind === 'image' ? 'Image analysed · test result shared with bus viewer' : this.alignedPreview() ? `${this.kind === 'video' ? 'Recorded playback' : 'Live camera'} · analysed frames with matching boxes` : this.kind === 'video' ? 'Recorded playback · analysing current frames' : 'Live camera · analysing newest frames');
        if(this.kind === 'image') {this.active = false; this.controls(); break;}
        // Background timers can be throttled to a second or more. The awaited
        // inference already bounds a hidden live camera to one request at a time.
        if(!document.hidden) await wait(15);
      }
    } catch(error) {if(this.lease.current(token) && error.name !== 'AbortError'){this.active = false;this.clearAnalyzed();this.error(error.message);this.status('Detection stopped');await this.deleteSession();this.controls();}}
  }
  currentSource(now) {
    const source = matchingSource(app.snapshot?.sources?.[this.role], {role:this.role,kind:this.kind,session:this.session,camera:this.camera,pending:this.pending});
    return agedSource(source,now-app.received,app.online);
  }
  postureState(now) {
    const source = this.currentSource(now), applied = this.kind === 'rtsp' ? this.camera?.options : this.sessionOptions;
    return posturePresentation({controller:app.snapshot?.assistance,online:app.online,elapsedMs:now-app.received,
      source,summary:this.kind === 'image' && this.result ? this.result.seating_summary : source?.seating_summary,
      model:app.backend?.seating,enabled:Boolean(applied?.posture_enabled),requested:this.el.posture.checked,
      confidence:applied?.standing_confidence,requestedConfidence:standingConfidence(this.el['standing-confidence'].value),
      eligible:postureAllowed(this.role,this.el.mode.value,this.el.prompt.value),
      hasInput:Boolean(this.hasMedia || this.camera?.enabled || this.session),kind:this.kind,pending:this.pending});
  }
  renderPosture(now) {
    if(this.role !== 'inside') return;
    const view = this.postureState(now);
    this.el['posture-option'].dataset.status = view.status;
    this.el['posture-title'].textContent = view.message;
    this.el['posture-label'].textContent = `Standing detection · ${view.engine}`;
    this.el['posture-detail'].textContent = view.detail;
    this.el['posture-legend'].hidden = !view.observed;
  }
  rtspPostureVisible(now) {
    return postureVisible(app.snapshot?.assistance,app.online,now-app.received)
      &&postureFrameFresh(this.currentSource(now)?.seating_summary,Math.max(0,now-app.received));
  }
  syncPreview() {
    return this.previewLease.sync({kind:this.kind,sessionId:this.camera?.session_id,
      sourceGeneration:this.lease.generation,paused:detectionPaused(this.role)});
  }
  counts(now) {
    if(detectionPaused(this.role)) return countPresentation(null, {mode:'empty',labels:evidenceLabels(null,this.sessionOptions || this.camera?.options,{prompt:this.el.prompt.value,mode:this.el.mode.value})});
    const source = this.currentSource(now); const local = this.kind !== 'rtsp' && this.result;
    const result = local ? this.result : source?.connected ? source : null; const image = local ? this.kind === 'image' : source?.kind === 'image';
    const applied = local ? this.sessionOptions : this.camera?.options;
    const labels = evidenceLabels(result,applied,{prompt:this.el.prompt.value,mode:this.el.mode.value});
    return countPresentation(result?.count_summary,{mode:result ? image ? 'image' : 'live' : 'empty',ageMs:local ? Math.max(0,now-this.captureAt) : source?.age_ms || 0,detections:result?.detections,labels});
  }
  render(now) {
    const journeyPaused = detectionPaused(this.role);
    this.syncPreview();
    if (this.journeyPaused && !journeyPaused) {
      const connected = this.hasMedia || this.camera?.enabled;
      this.status(connected ? 'Doors open · waiting for fresh detection' : 'Ready to connect');
      this.el['empty-title'].textContent = connected ? 'Waiting for a fresh frame' : 'A new perspective starts here.';
      this.el['empty-copy'].textContent = connected ? 'Detection has resumed for this stop.' : 'Connect your CCTV or choose a camera, image or video to begin.';
      if(this.hasMedia && !this.alignedPreview()) this.el.placeholder.hidden = true;
    }
    this.journeyPaused = journeyPaused;
    const source = this.currentSource(now); const view = this.counts(now); const test = this.kind === 'image' || this.kind === 'video'; const live = this.kind === 'rtsp' ? this.camera?.enabled && source?.connected : this.active && source?.connected;
    this.el.count.textContent = view.total.display; const result = this.kind === 'rtsp' ? source : this.result;
    const inference = inferencePresentation(result);this.el.inference.textContent = inference.text;this.el.inference.title = inference.title;
    const age = this.kind !== 'rtsp' && this.result ? now-this.captureAt : source?.age_ms;
    this.el.age.textContent = this.kind === 'image' && this.result ? 'Snapshot' : Number.isFinite(age) ? age < 1000 ? `${Math.round(age)} ms` : `${(age/1000).toFixed(1)} s` : '—';
    let state = test && this.hasMedia ? 'test' : live ? 'live' : this.pending || this.camera?.enabled ? 'warming' : 'idle';
    let text = test && this.hasMedia ? this.kind === 'image' ? 'Image test' : 'Recorded video' : live ? 'Live' : this.pending ? 'Connecting' : this.camera?.enabled ? 'Connecting' : 'Not connected';
    if(!live && !this.pending && this.active && this.hasMedia && this.kind === 'webcam') {state='warming';text='Awaiting detection';}
    if(this.camera?.enabled && this.camera.error){state = 'error'; text = 'Reconnecting'; this.status(this.camera.error);}
    else if(this.kind === 'rtsp' && this.camera?.enabled && !this.pending) this.status(this.camera.state === 'detecting' ? 'Live CCTV · processed frames stay aligned with boxes' : 'Connecting to CCTV · waiting for decoded video');
    if(this.kind === 'video' && this.hasMedia && this.el.video.paused && !this.pending && !this.el.video.ended) this.status('Playback paused · resume the video to refresh observations');
    if(this.alignedPreview() && this.active && !this.pending && !(this.kind === 'video' && this.el.video.paused) && this.result && !this.analyzed.current(now)) this.status('Showing the last analysed image · waiting for fresh detections; old boxes have expired');
    if(this.kind === 'video' && this.alignedPreview() && this.hasMedia) {
      this.el['play-pause'].textContent = this.el.video.paused ? 'Resume playback' : 'Pause playback';
      const duration = Number.isFinite(this.el.video.duration) ? this.el.video.duration : 0;
      this.el.seek.max = String(duration);this.el.seek.value = String(this.el.video.currentTime || 0);
      const position = Math.floor(this.el.video.currentTime || 0);this.el['playback-time'].textContent = `${Math.floor(position/60)}:${String(position%60).padStart(2,'0')}`;
    }
    if(this.kind === 'rtsp' && !this.camera?.preview_available && !this.el.image.hidden) {this.el.image.hidden = true;this.el.placeholder.hidden = false;}
    if(detectionPaused(this.role)) {
      state = 'warming'; text = 'Detection paused';
      this.status('Outside detection paused · resumes when the bus is stationary with doors fully open');
      this.el.inference.textContent = 'Paused'; this.el.age.textContent = '—';
      this.el.placeholder.hidden = false; this.el['empty-title'].textContent = 'Detection waits for open doors';
      this.el['empty-copy'].textContent = 'The source stays connected. Outside detection runs only while the bus is stationary with doors fully open.';
      if(this.kind === 'rtsp') this.el.image.hidden = true;
    }
    this.el.pill.textContent = text; this.el.pill.dataset.state = state;
    const aligned = this.alignedPreview() ? this.analyzed.preview(now) : null;
    const frameId = this.alignedPreview() ? aligned?.frame.frameId : result?.frame_id;
    this.el.frame.textContent = this.kind === 'webcam' && this.hasMedia && !this.alignedPreview() ? 'LIVE VIDEO' : frameId != null ? `${aligned && !aligned.fresh ? 'LAST FRAME' : 'FRAME'} ${frameId}` : '—';
    this.draw(now);this.renderPosture(now);
  }
  draw(now) {
    const canvas = this.el.overlay, rect = this.el.stage.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if(canvas.width !== Math.round(rect.width*dpr) || canvas.height !== Math.round(rect.height*dpr)){canvas.width = Math.round(rect.width*dpr);canvas.height = Math.round(rect.height*dpr);}
    const ctx = canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);
    if(detectionPaused(this.role) || this.kind === 'rtsp' || !this.hasMedia) return;
    let box, detections, seating;
    if(this.alignedPreview()) {
      this.el.video.hidden = true; this.el.image.hidden = true;
      const preview = this.analyzed.preview(now);this.el.placeholder.hidden = Boolean(preview);
      if(!preview) {this.el['empty-title'].textContent = this.result ? 'Waiting for a fresh analysed frame.' : 'Analysing the next frame…';this.el['empty-copy'].textContent = 'The image and its detections appear together. Older observations expire automatically.';return;}
      const frame=preview.frame;
      box = contentRect(rect.width,rect.height,frame.width,frame.height);ctx.drawImage(this.processed,box.x,box.y,box.w,box.h);
      if(!preview.fresh) return;
      detections = frame.detections;
      seating=frame.seating?{...frame.seating,age_ms:Number.isFinite(frame.seating.age_ms)?frame.seating.age_ms+Math.max(0,now-frame.receivedAt):Infinity}:null;
    } else {
      if(this.kind !== 'image') this.el.video.hidden = false;
      this.el.placeholder.hidden = true;
      if(!this.result) return;
      const media = this.kind === 'image' ? this.el.image : this.el.video; const width = media.naturalWidth || media.videoWidth, height = media.naturalHeight || media.videoHeight;
      if(!width || !height) return;
      box = contentRect(rect.width,rect.height,width,height);detections = this.kind === 'image' ? this.result.detections : liveBoxes(this.result.detections,this.captureAt,now);
      seating = this.kind==='image' ? this.result.seating_summary : null;
      if(this.kind === 'webcam') {
        const frame=this.analyzed.current(now),age=now-this.captureAt;
        if(Number.isFinite(age) && age>=0 && age<=250 && frame?.frameId===this.result.frame_id && frame.capturedAt===this.captureAt && frame.seating) {
          seating={...frame.seating,age_ms:Number.isFinite(frame.seating.age_ms)?frame.seating.age_ms+Math.max(0,now-frame.receivedAt):Infinity};
        }
      }
    }
    ctx.font = '500 10px "Segoe UI", sans-serif';ctx.lineWidth = 1.4;
    for(const detection of detections || []) {
      const [x1,y1,x2,y2] = detection.bbox; const x = box.x+x1*box.w,y=box.y+y1*box.h,w=(x2-x1)*box.w,h=(y2-y1)*box.h;
      const colour = colorFor(detection.label);ctx.strokeStyle = colour;ctx.setLineDash(detection.predicted ? [4,3] : []);ctx.strokeRect(x,y,w,h);
      const text = `${detection.label}${detectionScoreLabel(detection.score,this.sessionOptions?.mode)}`;const tw = ctx.measureText(text).width+12;const tx = Math.max(0,Math.min(x,rect.width-tw)),ty = Math.max(0,y-20);
      ctx.setLineDash([]);ctx.fillStyle = '#0b171bea';ctx.fillRect(tx,ty,tw,18);ctx.fillStyle = colour;ctx.fillText(text,tx+6,ty+12);
    }
    if(this.sessionOptions?.posture_enabled && postureVisible(app.snapshot?.assistance, app.online, now-app.received))drawPosture(ctx,seating,box);
  }
  async preview() {
    const previewToken = this.syncPreview();
    const postureNow = this.rtspPostureVisible(performance.now());
    const previousId = this.role === 'inside' && this.camera?.options?.posture_enabled && this.previewPostureAllowed !== postureNow ? null : this.previewId;
    if(this.kind !== 'rtsp' || !this.previewLease.current(previewToken) || this.previewBusy || !shouldPreview(this.camera,previousId,!document.hidden)) return;
    const camera = this.camera, token = this.lease.generation; this.previewBusy = true;
    try {const response = await fetch(`/api/bus/cameras/${this.role}/preview.jpg?frame=${camera.preview_frame_id}`,{cache:'no-store',signal:AbortSignal.timeout(2500)});if(!response.ok) return;const blob = await response.blob();
      this.syncPreview();
      if(!this.lease.current(token) || !this.previewLease.current(previewToken) || this.kind !== 'rtsp' || !this.camera?.enabled || !this.camera.preview_available || this.camera.session_id !== camera.session_id) return;
      const url = URL.createObjectURL(blob);const image = new Image();image.src = url;try{await image.decode();}catch{URL.revokeObjectURL(url);return;}
      this.syncPreview();
      if(!this.lease.current(token) || !this.previewLease.current(previewToken) || this.kind !== 'rtsp' || !this.camera?.enabled || !this.camera.preview_available || this.camera.session_id !== camera.session_id || this.role === 'inside' && camera.options?.posture_enabled
        && postureNow !== this.rtspPostureVisible(performance.now())){URL.revokeObjectURL(url);return;}
      this.previewPostureAllowed = postureNow;
      const old = this.previewURL;this.previewURL = url;this.el.image.src = url;this.el.image.hidden = false;this.el.placeholder.hidden = true;this.previewId = camera.preview_frame_id;if(old)URL.revokeObjectURL(old);
    } catch {} finally {this.previewBusy = false;}
  }
}

const cards = ['outside','inside'].map((role,index) => new InputCard(role,index));
for(const button of document.querySelectorAll('[data-count-role]')) button.onclick = () => {app.role = button.dataset.countRole;document.querySelectorAll('[data-count-role]').forEach(node => node.setAttribute('aria-pressed',node === button));renderIntelligence(performance.now());};

function renderIntelligence(now) {
  const card = cards.find(card => card.role === app.role), view = card.counts(now);
  const posture = cards[1].postureState(now);
  const inside = cards[1].currentSource(now), outside = cards[0].currentSource(now);
  const postureSummary=cards[1].kind==='image' && cards[1].result ? cards[1].result.seating_summary : inside?.seating_summary;
  const postureRows=postureCountRows(postureSummary,{role:app.role,status:posture.status,
    mode:cards[1].kind==='image'?'image':'live',ageMs:posture.ageMs ?? inside?.age_ms ?? 0});
  $('stableCount').textContent = view.total.display; $('rawCount').textContent = `Observed now ${view.total.raw ?? '—'}`; $('voteSupport').textContent = view.total.supportText;
  $('countState').textContent = {stable:'Stable count',warming:'Building evidence',stale:'Stale observations',instant:'Single image test',uncertain:'Uncertain · holding briefly',legacy:'Observed only'}[view.status] || 'Awaiting input'; $('countState').dataset.state = view.status;
  const signature = JSON.stringify([view.classes,view.mode,postureRows]);
  if($('countRows').dataset.signature !== signature) {
    $('countRows').dataset.signature = signature;
    const entries=[...Object.entries(view.classes).map(([label,entry])=>({...entry,label:entry.label||label})),...postureRows];
    const rows=entries.map((entry,index)=>{const row=document.createElement('tr');if(entry.posture){row.dataset.kind='posture';if(index===entries.length-postureRows.length)row.className='posture-count-start';}const name=document.createElement('td');const dot=document.createElement('span');dot.className='label-dot';dot.style.background=entry.colour||colorFor(entry.label);name.append(dot,document.createTextNode(entry.label));row.append(name);for(const value of [entry.display,entry.raw??'—',entry.supportText]){const td=document.createElement('td');td.textContent=value;row.append(td);}return row;});$('countRows').replaceChildren(...rows);
  }
  $('countHint').textContent = `${view.message} Object categories may overlap; this total is not a unique passenger count.${app.role==='inside'?' Standing detections describe people already counted; they are not added to the object or passenger total.':''}`;
  $('insideEvidence').textContent = sourceEvidence(inside);$('outsideEvidence').textContent = sourceEvidence(outside);
  const online = app.online && now-app.received<1500;
  const decision = online ? app.snapshot?.assistance?.readiness : null;
  $('decisionTitle').textContent = !online ? 'Controller offline' : {ready:'Ready for simulated departure',travelling:'Travelling · simulation',held:'Departure held'}[decision?.status] || 'Waiting for controller';
  $('decisionMessage').textContent = decision?.message || 'The shared controller is unavailable. Bus movement remains unconfirmed.';
  $('decisionStatus').dataset.state = decision?.can_depart ? 'ready' : 'hold';
  $('standingCount').textContent = Number.isInteger(posture.standing) ? posture.standing : '—';
  $('confirmation').textContent = posture.observed && !['image','video'].includes(cards[1].kind)
    ? `${posture.message} · ${decision?.posture?.progress_ms || 0} / ${departureConfirmationMs(app.snapshot?.assistance).toLocaleString('en-US')} ms with no standing detected` : posture.message;
  $('confirmation').title = posture.detail;
  renderOperations(app.snapshot?.assistance,online);
  const events = app.snapshot?.events || []; $('eventCount').textContent = String(events.length);
  const eventKey = `${app.snapshot?.instance_id}:${events.at(-1)?.id || 0}`;
  if($('events').dataset.key !== eventKey) {
    $('events').dataset.key = eventKey;const rows = events.slice(-5).reverse().map(event => {const li=document.createElement('li');const label=document.createElement('span');const name={wheelchair:'Wheelchair assistance',pram:'Pram boarding support',walking:'Walking-aid assistance',senior:'Senior RFID request',assistance:'Assistance RFID request'}[event.type]||event.type;label.textContent=`${name}${['image','video'].includes(event.source_kind) ? ' · test' : ''}`;const time=document.createElement('time');time.textContent=new Date(event.timestamp_ms).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});li.append(label,time);return li;});
    if(!rows.length){const li=document.createElement('li');li.className='empty-event';li.textContent='Assistance requests will appear here.';rows.push(li);}$('events').replaceChildren(...rows);
  }
}

async function pollState() {
  if(document.hidden && !cards.some(backgroundCameraActive)){setTimeout(pollState,500);return;}
  const start = performance.now();
  try {
    app.snapshot = await api('/api/bus/state',{signal:AbortSignal.timeout(3500)}); app.received = performance.now(); app.online = true;
  } catch {app.online = false;}
  cards.forEach(card=>card.syncPreview());
  setTimeout(pollState,Math.max(25,(document.hidden ? 500 : 250)-(performance.now()-start)));
}
async function pollCameras() {
  if(document.hidden){setTimeout(pollCameras,500);return;}
  const start = performance.now();
  try {
    const cameras = await api('/api/bus/cameras',{signal:AbortSignal.timeout(2500)});
    for(const card of cards) {
      const camera = cameras.cameras.find(camera => camera.role === card.role);
      if(!card.pending && card.kind === 'rtsp') {card.camera = camera;if(!card.touched && camera?.enabled){card.restore(camera.options);card.active=true;card.touched=true;} card.controls();}
    }
  } catch {}
  setTimeout(pollCameras,Math.max(25,(cards.some(card=>card.kind === 'rtsp' && card.camera?.enabled) ? 125 : 500)-(performance.now()-start)));
}
async function pollStatus() {
  try {const state = await api('/api/status',{signal:AbortSignal.timeout(4000)});app.backend = state.backend;app.ready = state.state === 'ready' || state.ready === true;
    // Backends report their initialized state in the dispatcher state field.
    $('modelState').dataset.ready = String(app.ready);$('modelState').replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(app.ready ? 'Model online' : state.state === 'loading' ? 'Loading model' : 'Model unavailable'));
    $('demoNotice').hidden = !state.backend?.demo;$('backendDetail').textContent = [state.backend?.name || state.configured_backend, state.backend?.device].filter(Boolean).join(' · ');
    for(const card of cards) {
      card.el['phrase-help'].textContent = state.backend?.phrase_scope === 'person-clothing-color' ? 'This legacy model supports one clothing-colour phrase, such as “a person with a red shirt”. Select the grounding model to use multiple detailed phrases.' : state.backend?.phrases ? `${phraseGuide} The grounding model evaluates the targets together. For a live camera, choose Detection frame to see the analysed image with matching boxes. Recorded videos use analysed frames.` : 'This backend does not support detailed phrases. Select object categories or use a phrase-capable model.';
    }
    cards.forEach(card=>card.controls());
  } catch {app.ready = false;$('modelState').dataset.ready = 'false';$('modelState').replaceChildren(document.createElement('i'),document.createTextNode('Server offline'));cards.forEach(card=>card.controls());}
  setTimeout(pollStatus,4000);
}
function previewTick(){for(const card of cards) card.preview();setTimeout(previewTick,125);}
function render(){if(!document.hidden){const now=performance.now();for(const card of cards)card.render(now);renderIntelligence(now);}setTimeout(render,100);}
navigator.mediaDevices?.addEventListener('devicechange',()=>cards.forEach(card=>card.devices()));
window.addEventListener('pagehide',()=>{for(const card of cards){card.lease.renew();card.lease.releaseStream();if(card.session)fetch(`/api/sessions/${card.session}`,{method:'DELETE',keepalive:true}).catch(()=>{});if(card.url)URL.revokeObjectURL(card.url);if(card.previewURL)URL.revokeObjectURL(card.previewURL);}});
initOperations();pollStatus();pollState();pollCameras();previewTick();render();
