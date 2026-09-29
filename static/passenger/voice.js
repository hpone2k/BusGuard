import {wakeInvocation, standbyPhrase, VoiceWakeGate, parseVoiceTool, publicVoiceContext, voiceToolResult} from './voice-core.js?v=voice-duplex-1';
import {SpokenGuidance} from './spoken-guidance.js?v=voice-flow-1';
import {audioTurns} from './audio-coordination.js?v=voice-flow-1';

export function mountVoiceAssistant({host, getContext, getFreshRequest, onRequest, onComplete, onCancel, onReminder, onPreference, onPanel, onActivityChange, onStatusChange}) {
  if (!host) return null;
  host.classList.add('voice-assistant');
  host.innerHTML = `<div class="voice-heading"><h2>Talk to BusGuard</h2>
      <div class="voice-actions"><button type="button" class="primary-button" data-voice="start" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8"/></svg>Enable voice</button><button type="button" class="primary-button" data-voice="talk" hidden>Talk now</button><button type="button" data-voice="stop" hidden>Stop listening</button></div>
    </div>
    <p class="voice-intro">Say <strong>“Hey BusGuard”</strong>, then tell me what you need. I can request assistance, check your bus or remind you at your stop. I’ll finish my reply before listening again. After my reply, continue within 3 seconds, say “standby”, or use “Hey BusGuard” to begin again.</p>
    <p class="voice-status" data-voice="status" role="status" aria-live="polite">Checking voice availability…</p>
    <button type="button" class="voice-retry" data-voice="retry" hidden>Check voice again</button>
    <div class="voice-captions" data-voice="captions" role="log" aria-label="Voice conversation captions" aria-live="polite"></div>
    <details class="voice-options"><summary>Voice options &amp; privacy</summary>
      <p class="voice-examples">Try “Request the ramp at stop A”, “Remind me at stop C”, or “Turn on arrival vibration”. Choose one assistance option per request; ramp access includes 20 seconds.</p>
      <div class="voice-preferences"><label>Voice <select data-voice="choice"><option value="marin">Marin</option><option value="cedar">Cedar</option></select></label><button type="button" data-voice="preview">Hear this voice</button></div>
      <p class="voice-disclosure">This is an AI voice. Standby waits for “Hey BusGuard” without replying to other speech. Your microphone audio goes to OpenAI to recognize that phrase, but is muted while BusGuard prepares and plays its reply. Speak after the reply ends; speech during it is ignored. Tap Talk now to activate manually. Stop listening turns the microphone off immediately. Switching apps or locking your phone pauses listening; keep this page visible for voice and arrival alerts.</p>
      <a class="voice-setup-link" href="/setup">Phone setup &amp; microphone access <span aria-hidden="true">↗</span></a>
    </details>`;
  const el = name => host.querySelector(`[data-voice="${name}"]`);
  const guide = new SpokenGuidance();
  let config, pc = null, channel = null, microphone = null, audio = null, abort = null;
  let generation = 0, connected = false, starting = false, sessionPending = false, sessionUntil = 0;
  const wakeGate = new VoiceWakeGate();
  let responding = false, caption = null, timer = null, toolsBusy = false;
  let toolQueue = Promise.resolve();
  let sessionHandle = null;
  let refreshing = false;
  let turnNumber = 0;
  let voiceChoiceEdited = false, activePermission = null, pendingTurn = null;
  let responsePermission = null, activeResponseId = null, latestSpeechId = null;
  let acceptedContext = [];
  let desired = true;
  try { desired = localStorage.getItem('busguard.voice.listening') !== 'off'; } catch { /* Storage may be unavailable. */ }
  let disposed = false, pageAway = false, automaticBlocked = false, permissionGranted = false, checkingPermission = false;
  let retryTimer = null, retryCount = 0, connectedAt = 0, closing = Promise.resolve();
  let audioLease = null, audioWait = null, audioSequence = 0, playbackResponseId = null;
  let responseFinished = false, playbackStarted = false, playbackFinished = false, playbackTimer = null;
  let playbackBlocked = false;
  let playbackCleared = false, pendingSpokenContext = null;
  let responseTimer = null;
  let inputBlocked = false, inputResumeTarget = null, inputResumeTimer = null;
  let inputClearRequests = 0, inputClearReplies = 0;
  let announcementActive = audioTurns.state?.kind === 'announcement';
  const ignoredTranscripts = new Set();
  const processedTranscripts = new Set();
  const permissions = new Map();
  const clientId = [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, '0')).join('');
  const executed = new Set();

  function status(message, tone = '', needsAction = ['setup', 'error'].includes(tone)) {
    el('status').textContent = message; host.dataset.voiceState = tone;
    try { onStatusChange?.({message, state: tone || 'off', needsAction, secure: Boolean(window.isSecureContext)}); }
    catch { /* Status displays cannot interrupt microphone cleanup. */ }
  }
  function rememberListening(value) {
    desired = value;
    try { localStorage.setItem('busguard.voice.listening', value ? 'on' : 'off'); } catch { /* Keep the in-page preference. */ }
  }
  function line(speaker, text) {
    const row = document.createElement('p'), label = document.createElement('strong');
    label.textContent = `${speaker}: `; row.append(label, document.createTextNode(text));
    el('captions').append(row);
    while (el('captions').children.length > 8) el('captions').firstElementChild.remove();
    return row;
  }
  function send(event) {
    if (channel?.readyState !== 'open') return false;
    if (event.type === 'input_audio_buffer.clear') inputClearRequests++;
    channel.send(JSON.stringify(event)); return true;
  }
  function controls() {
    el('start').hidden = connected; el('start').disabled = starting || sessionPending || !config?.enabled
      || !window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection;
    el('stop').hidden = !connected && !starting; el('talk').hidden = !connected;
    el('talk').disabled = !playbackBlocked && Boolean(conversationBusy() || inputBlocked || announcementActive);
    el('talk').textContent = playbackBlocked ? 'Enable reply audio' : 'Talk now';
    el('choice').disabled = connected || starting;
    try { onActivityChange?.({listening: connected, connecting: starting}); }
    catch { /* A display callback must not interrupt microphone or session cleanup. */ }
  }
  function conversationBusy() { return Boolean(responding || audioLease || audioWait || toolsBusy || pendingTurn); }
  function listeningBlocked() { return Boolean(announcementActive || conversationBusy() || inputBlocked); }
  function ignoreSpeech(itemId) {
    if (!itemId) return;
    ignoredTranscripts.add(itemId); wakeGate.discard(itemId);
    if (ignoredTranscripts.size > 100) ignoredTranscripts.delete(ignoredTranscripts.values().next().value);
  }
  function showListening() {
    if (listeningBlocked()) return;
    if (wakeGate.awake) status('I’m listening. Continue within 3 seconds, or say “Hey BusGuard” again.', 'active');
    else showStandby();
  }
  function playReplyAudio() {
    const instance = generation, output = audio;
    return output?.play().then(() => {
      if (instance !== generation || audio !== output) return;
      playbackBlocked = false; controls();
    }).catch(() => {
      if (instance !== generation || audio !== output) return;
      playbackBlocked = true; controls();
      status('Tap Enable reply audio to hear BusGuard. Captions are still available.', 'setup');
    });
  }
  function showStandby() { status('Standby · Say “Hey BusGuard” when you need help.', 'standby'); }
  function standby() {
    wakeGate.standby(); acceptedContext = []; interrupt(); send({type: 'input_audio_buffer.clear'});
    showStandby();
  }
  function expireConversation() {
    if (wakeGate.expire(conversationBusy() || inputBlocked)) { acceptedContext = []; interrupt(); showStandby(); }
  }
  function syncMicrophone() {
    const busy = announcementActive || conversationBusy();
    if (microphone && busy && !inputBlocked) {
      inputBlocked = true;
      // Invalidate even already-started audio whose transcription might arrive
      // after this reply. Nothing spoken during a reply becomes a queued turn.
      for (const itemId of wakeGate.speech.keys()) ignoreSpeech(itemId);
      latestSpeechId = null;
      send({type: 'input_audio_buffer.clear'});
    }
    if (busy && inputResumeTarget !== null) {
      inputResumeTarget = null; clearTimeout(inputResumeTimer); inputResumeTimer = null;
    }
    microphone?.getTracks().forEach(track => { track.enabled = !busy && !inputBlocked; });
    if (microphone && connected && inputBlocked && !busy && inputResumeTarget === null) {
      // Wait for a remote clear acknowledgement before reopening capture. VAD
      // or ASR events already in flight are still rejected during this barrier.
      inputResumeTarget = inputClearRequests + 1;
      if (send({type: 'input_audio_buffer.clear'})) {
        const instance = generation;
        inputResumeTimer = setTimeout(() => {
          if (instance === generation && inputResumeTarget !== null)
            scheduleReconnect('Refreshing the microphone connection…', 0);
        }, 4000);
      } else inputResumeTarget = null;
    }
    controls();
  }
  function watchResponse() {
    clearTimeout(responseTimer);
    const instance = generation;
    responseTimer = setTimeout(() => {
      if (instance !== generation || !responding) return;
      interrupt(); responding = false; activeResponseId = responsePermission = null;
      syncMicrophone();
      if (wakeGate.awake) status('The voice response stalled. Listening is ready; please ask again.', 'error'); else showStandby();
    }, 40000);
  }
  function releaseAudio(interrupted = false) {
    audioSequence++; audioWait?.abort(); audioWait = null;
    clearTimeout(playbackTimer); playbackTimer = null;
    if (interrupted) { audio?.pause(); send({type: 'output_audio_buffer.clear'}); }
    if (pendingSpokenContext && !interrupted && !playbackCleared)
      pendingSpokenContext.context.push(...pendingSpokenContext.messages);
    pendingSpokenContext = null;
    const lease = audioLease; audioLease = null; lease?.release();
    playbackResponseId = null; responseFinished = playbackStarted = playbackFinished = playbackCleared = false;
    syncMicrophone();
    if (!interrupted) flushPendingTurn();
  }
  function stop(message = 'Listening is off. Tap Enable voice when you need help.', {preservePreference = false, tone = '', needsAction = !preservePreference} = {}) {
    if (!preservePreference) rememberListening(false);
    clearTimeout(retryTimer); retryTimer = null;
    generation++; clearInterval(timer); timer = null;
    clearTimeout(responseTimer); responseTimer = null;
    clearTimeout(inputResumeTimer); inputResumeTimer = null;
    // Let a bounded session-creation response return its private handle so the
    // generation-mismatch branch can close it after local listening stops.
    if (!sessionPending) { abort?.abort(); abort = null; }
    microphone?.getTracks().forEach(track => track.stop()); microphone = null;
    releaseAudio(true);
    channel?.close(); channel = null; pc?.close(); pc = null;
    if (audio) { audio.pause(); audio.srcObject = null; audio = null; }
    connected = starting = responding = toolsBusy = false; sessionUntil = 0; wakeGate.reset(); acceptedContext = [];
    playbackBlocked = false;
    if (sessionHandle) {
      closing = closeSession(sessionHandle); sessionHandle = null;
    }
    executed.clear(); permissions.clear(); ignoredTranscripts.clear(); processedTranscripts.clear(); toolQueue = Promise.resolve();
    inputBlocked = false; inputResumeTarget = null; inputClearRequests = inputClearReplies = 0;
    activePermission = pendingTurn = responsePermission = activeResponseId = latestSpeechId = null;
    caption = null; controls(); status(sessionPending ? `${message} Finishing the previous connection…` : message, tone, needsAction);
  }
  async function closeSession(id) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 4000);
    try { await fetch('/api/voice/session/close', {method: 'POST', keepalive: true,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: id, client_id: clientId}), signal: controller.signal}); }
    catch { /* The server also expires each bounded session independently. */ }
    finally { clearTimeout(timeout); }
  }
  function scheduleReconnect(message = 'Voice connection interrupted. Reconnecting…', delay) {
    if (connected && Date.now() - connectedAt > 30000) retryCount = 0;
    stop(message, {preservePreference: true, tone: 'connecting'});
    if (!desired || disposed || pageAway || document.hidden || automaticBlocked) return;
    if (++retryCount > 4) {
      automaticBlocked = true;
      status('Voice could not reconnect. Tap Enable voice to try again, or use the assistance buttons.', 'error');
      return;
    }
    retryTimer = setTimeout(() => { retryTimer = null; void autoResume(); }, delay ?? Math.min(1000 * 2 ** (retryCount - 1), 8000));
  }
  async function autoResume() {
    if (!desired || disposed || pageAway || document.hidden || automaticBlocked || checkingPermission || connected || starting || sessionPending) return;
    if (!config?.enabled || !window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) return;
    checkingPermission = true;
    try {
      if (!permissionGranted) {
        const permission = await navigator.permissions?.query({name: 'microphone'}).catch(() => null);
        permissionGranted = permission?.state === 'granted';
      }
      if (!desired || disposed || pageAway || document.hidden || automaticBlocked) return;
      if (permissionGranted) void start({automatic: true});
      else status('Allow your microphone to begin. Listening resumes automatically while this app is open.', 'setup');
    } finally { checkingPermission = false; }
  }
  function interrupt(rejectTranscript = true) {
    // Cancellation can race response.done. Revoke permission synchronously,
    // before waiting for the remote cancellation acknowledgement.
    permissions.clear(); activePermission = null;
    clearTimeout(responseTimer); responseTimer = null;
    if (pendingTurn?.itemId) send({type: 'conversation.item.delete', item_id: pendingTurn.itemId});
    pendingTurn = null;
    if (rejectTranscript && latestSpeechId) ignoredTranscripts.add(latestSpeechId);
    if (ignoredTranscripts.size > 100) ignoredTranscripts.delete(ignoredTranscripts.values().next().value);
    if (responding) {
      send({type: 'response.cancel', ...(activeResponseId ? {response_id: activeResponseId} : {})});
      send({type: 'output_audio_buffer.clear'});
      watchResponse();
      // Keep responding true until response.done; creating another response
      // early can lose the new utterance to an active-response conflict.
    }
    const wasWaiting = Boolean(audioWait);
    releaseAudio(true);
    if (wasWaiting) { responding = false; responsePermission = activeResponseId = null; }
    syncMicrophone();
    caption = null;
  }
  async function refresh() {
    if (refreshing || connected || starting) return;
    refreshing = true;
    try {
      const response = await fetch('/api/voice/config', {cache: 'no-store', signal: AbortSignal.timeout(3500)});
      if (!response.ok) throw new Error('The voice service is unavailable.');
      config = await response.json();
      guide.config = config; guide.configAt = Date.now();
      if (!voiceChoiceEdited || !['marin', 'cedar'].includes(el('choice').value))
        el('choice').value = ['marin', 'cedar'].includes(config.default_voice) ? config.default_voice : 'marin';
      el('retry').hidden = config.enabled === true;
      if (!config.enabled) status('Voice is not set up yet. You can still use the assistance buttons.', 'setup');
      else if (!window.isSecureContext) status('Microphone access needs HTTPS. Open the secure voice site, or follow Phone setup.', 'setup');
      else if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) status('Voice is unavailable in this browser. You can still use the assistance buttons.', 'setup');
      else status(desired ? 'Checking microphone permission…' : 'Listening is off. Tap Enable voice to listen.', desired ? 'connecting' : '', !desired);
    } catch (error) { config = null; el('retry').hidden = false; status(error.message || 'Cannot reach the voice service.', 'setup'); }
    refreshing = false; controls(); void autoResume();
  }
  async function freshContext() {
    const originalId = getContext().request?.id || null;
    const request = getFreshRequest ? await getFreshRequest() : null;
    const response = await fetch('/api/assistance/state', {cache: 'no-store'});
    if (!response.ok) throw new Error('The bus controller is offline. Its location and availability cannot be confirmed.');
    const context = getContext();
    if ((context.request?.id || null) !== originalId) throw new Error('Your request changed while it was being checked. Please ask again.');
    return publicVoiceContext({...context, state: await response.json(), request});
  }
  function newTurn() {
    const context = getContext();
    const id = String(++turnNumber);
    permissions.clear();
    permissions.set(id, {id, expires: Date.now() + 30000, context: acceptedContext, requestId: context.request?.id || null,
      location: JSON.stringify(context.location || null)});
    activePermission = permissions.get(id);
    return activePermission;
  }
  function permissionActive(permission) {
    return permission && permission === activePermission && permissions.get(permission.id) === permission
      && Date.now() < permission.expires;
  }
  async function createResponse(permission) {
    if (!permissionActive(permission) || responding || toolsBusy) return;
    if (audioLease) { pendingTurn = {permission}; return; }
    responding = true; responsePermission = permission; activeResponseId = null;
    syncMicrophone();
    const sequence = ++audioSequence, instance = generation;
    const controller = new AbortController(); audioWait = controller;
    const waitExpiry = setTimeout(() => controller.abort(), Math.max(0, permission.expires - Date.now()));
    try {
      const lease = await audioTurns.acquire({kind: 'realtime', owner: clientId, signal: controller.signal});
      if (instance !== generation || sequence !== audioSequence || !permissionActive(permission)) {
        lease.release();
        if (instance === generation && sequence === audioSequence) {
          audioWait = null; responding = false; responsePermission = null;
          status('That spoken request expired while audio was busy. Please ask again.', 'listening');
        }
        return;
      }
      audioWait = null; audioLease = lease;
      responseFinished = playbackStarted = playbackFinished = playbackCleared = false;
      void playReplyAudio();
      // Never inherit the server's default audio conversation: standby speech
      // can finish ASR out of order and still be present there. The response and
      // every tool continuation receive only explicitly admitted text and calls.
      if (!send({type: 'response.create', response: {conversation: 'none', metadata: {busguard_turn: permission.id}, input: [...permission.context]}})) {
        releaseAudio(); responding = false; responsePermission = null;
      } else watchResponse();
    } catch {
      if (instance === generation && sequence === audioSequence) {
        audioWait = null; responding = false; responsePermission = null;
        status('Voice is waiting for audio to become available. Please ask again.', 'error');
      }
    } finally { clearTimeout(waitExpiry); syncMicrophone(); }
  }
  function flushPendingTurn() {
    if (responding || audioLease || toolsBusy || !pendingTurn) return;
    const turn = pendingTurn; pendingTurn = null;
    createResponse(turn.permission);
  }
  function acknowledgeInterruptedCalls(calls, permission) {
    for (const item of calls) {
      if (executed.has(item.call_id)) continue;
      executed.add(item.call_id);
      if (executed.size > 100) { scheduleReconnect('Refreshing your voice conversation…', 0); return; }
      if (permission) permission.context.push(
        {type: 'function_call', call_id: item.call_id, name: item.name, arguments: item.arguments || '{}'},
        {type: 'function_call_output', call_id: item.call_id,
          output: JSON.stringify({ok: false, message: 'This command was interrupted before it was sent to the bus.'})});
    }
  }
  async function invoke(item, permission) {
    const args = parseVoiceTool(item.name, item.arguments || '{}');
    if (!permissionActive(permission)) throw new Error('That spoken command was interrupted or expired. Please say your request again.');
    if (item.name === 'get_bus_status') return {ok: true, ...await freshContext()};
    const context = getContext();
    if (context.isBusy) throw new Error('Another passenger action is still being confirmed. Please wait.');
    if (JSON.stringify(context.location || null) !== permission.location) throw new Error('Your demo location changed. Please say your request again.');
    if (['set_arrival_reminder', 'cancel_arrival_reminder'].includes(item.name)) {
      if (!onReminder) throw new Error('Arrival reminders are unavailable. Please use the app settings.');
      return voiceToolResult(await onReminder({action: item.name === 'set_arrival_reminder' ? 'set' : 'cancel', ...args}));
    }
    if (item.name === 'set_app_preference') {
      if (!onPreference) throw new Error('App settings are unavailable. Please use Settings.');
      return voiceToolResult(await onPreference(args));
    }
    if (item.name === 'open_app_panel') {
      if (!onPanel) throw new Error('App navigation is unavailable. Please use the screen controls.');
      return voiceToolResult(await onPanel(args));
    }
    if (item.name !== 'request_assistance' && (!permission.requestId || context.request?.id !== permission.requestId)) {
      throw new Error('The request has changed since you spoke. Please review it and say the command again.');
    }
    const result = item.name === 'request_assistance' ? await onRequest(args)
      : item.name === 'complete_request' ? await onComplete() : await onCancel();
    return voiceToolResult(result);
  }
  async function handle(event, instance) {
    if (instance !== generation) return;
    if (event.type === 'session.created' || event.type === 'session.updated') return;
    if (event.type === 'input_audio_buffer.cleared') {
      inputClearReplies++;
      if (inputResumeTarget !== null && inputClearReplies >= inputResumeTarget && !conversationBusy() && !announcementActive) {
        clearTimeout(inputResumeTimer); inputResumeTimer = null; inputResumeTarget = null;
        inputBlocked = false; wakeGate.touch(); syncMicrophone(); showListening();
      }
      return;
    }
    if (event.type === 'input_audio_buffer.speech_started') {
      if (listeningBlocked() || ignoredTranscripts.has(event.item_id)) { ignoreSpeech(event.item_id); return; }
      latestSpeechId = event.item_id || null;
      if (wakeGate.started(event.item_id, conversationBusy())) { acceptedContext = []; interrupt(false); showStandby(); }
    }
    if (event.type === 'input_audio_buffer.speech_stopped') {
      if (listeningBlocked()) ignoreSpeech(event.item_id);
      else wakeGate.stopped(event.item_id);
    }
    if (event.type === 'conversation.item.input_audio_transcription.failed') {
      wakeGate.discard(event.item_id); expireConversation();
    }
    if (event.type === 'conversation.item.input_audio_transcription.completed') {
      const text = String(event.transcript || '').trim();
      if (!text) { wakeGate.discard(event.item_id); expireConversation(); return; }
      if (listeningBlocked() || !event.item_id || !wakeGate.speech.has(event.item_id)) {
        ignoreSpeech(event.item_id);
        if (event.item_id) send({type: 'conversation.item.delete', item_id: event.item_id});
        return;
      }
      if (event.item_id && (ignoredTranscripts.has(event.item_id) || processedTranscripts.has(event.item_id))) {
        wakeGate.discard(event.item_id);
        send({type: 'conversation.item.delete', item_id: event.item_id}); return;
      }
      if (event.item_id) {
        processedTranscripts.add(event.item_id);
        if (processedTranscripts.size > 100) processedTranscripts.delete(processedTranscripts.values().next().value);
      }
      const invocation = wakeInvocation(text);
      const wasAwake = wakeGate.awake, previousEpoch = wakeGate.epoch;
      const awake = wakeGate.accept(event.item_id, invocation, conversationBusy());
      if (previousEpoch !== wakeGate.epoch) acceptedContext = [];
      if (!awake) {
        if (wasAwake && !wakeGate.awake) { acceptedContext = []; interrupt(false); showStandby(); }
        if (event.item_id) send({type: 'conversation.item.delete', item_id: event.item_id}); return;
      }
      if (standbyPhrase(invocation ? invocation.command : text)) {
        line('You', text);
        if (event.item_id) send({type: 'conversation.item.delete', item_id: event.item_id});
        standby(); return;
      }
      line('You', text); status('BusGuard is listening to your request.', 'active');
      interrupt(false);
      if (invocation && !invocation.command) {
        if (event.item_id) send({type: 'conversation.item.delete', item_id: event.item_id});
        status('I’m listening. Tell BusGuard what you need.', 'active');
        return;
      }
      acceptedContext.push({type: 'message', role: 'user', content: [{type: 'input_text', text: invocation ? invocation.command : text}]});
      if (event.item_id) send({type: 'conversation.item.delete', item_id: event.item_id});
      pendingTurn = {permission: newTurn(), itemId: event.item_id || null};
      flushPendingTurn();
    }
    if (event.type === 'response.created') {
      const response = event.response;
      if (!responding || !permissionActive(responsePermission) || !response?.id || response.metadata?.busguard_turn !== responsePermission?.id
          || activeResponseId && activeResponseId !== response.id) {
        if (response?.id) send({type: 'response.cancel', response_id: response.id});
        return;
      }
      activeResponseId = response.id;
      playbackResponseId = response.id;
      watchResponse();
      if (!permissionActive(responsePermission)) send({type: 'response.cancel', response_id: response.id});
    }
    if (responding && event.response_id === activeResponseId && (event.type.startsWith('response.')
        || event.type === 'output_audio_buffer.started')) watchResponse();
    if (event.response_id === playbackResponseId && event.type === 'output_audio_buffer.started') {
      playbackStarted = true; syncMicrophone();
      status('BusGuard is speaking. I’ll listen again when my reply finishes.', 'speaking');
    }
    if (event.response_id === playbackResponseId && ['output_audio_buffer.stopped', 'output_audio_buffer.cleared'].includes(event.type)) {
      playbackFinished = true;
      playbackCleared = event.type === 'output_audio_buffer.cleared';
      syncMicrophone();
      if (responseFinished) { releaseAudio(); showListening(); }
    }
    if (event.type === 'response.output_audio_transcript.delta' || event.type === 'response.audio_transcript.delta') {
      if (!permissionActive(responsePermission) || event.response_id !== activeResponseId) return;
      if (!caption) caption = line('BusGuard', '');
      caption.append(document.createTextNode(event.delta || ''));
    }
    if ((event.type === 'response.output_audio_transcript.done' || event.type === 'response.audio_transcript.done')
        && event.response_id === activeResponseId) caption = null;
    if (event.type === 'response.done') {
      const response = event.response;
      if (!responsePermission || !response?.id || response.metadata?.busguard_turn !== responsePermission.id
          || activeResponseId && activeResponseId !== response.id) return;
      const permission = permissions.get(response.metadata.busguard_turn);
      const calls = (Array.isArray(response.output) ? response.output : []).filter(item => item.type === 'function_call'
        && typeof item.call_id === 'string' && item.call_id.length <= 150 && !executed.has(item.call_id));
      // Hold capture throughout tools and their spoken confirmation, including
      // tool-only responses with no audio to wait for.
      toolsBusy = response.status === 'completed' && permissionActive(permission) && calls.length > 0;
      clearTimeout(responseTimer); responseTimer = null;
      responseFinished = true;
      const hasAudio = playbackStarted || (Array.isArray(response.output) && response.output.some(item =>
        Array.isArray(item.content) && item.content.some(content => ['audio', 'output_audio'].includes(content.type))));
      const audioPending = hasAudio && !playbackFinished && Boolean(audioLease);
      const audioInterrupted = playbackCleared || ['cancelled', 'failed'].includes(response.status);
      // An incomplete generation may still contain audible speech queued on
      // WebRTC. Preserve that audio until the playback-ended event as well.
      if (!hasAudio || playbackFinished || ['cancelled', 'failed'].includes(response.status)) releaseAudio(['cancelled', 'failed'].includes(response.status));
      else playbackTimer = setTimeout(() => {
        if (audioLease && playbackResponseId === response.id) {
          releaseAudio(true);
          status('Voice playback stopped after its time limit. Please ask again.', 'error');
        }
      }, 120000);
      responding = false; caption = null;
      activeResponseId = responsePermission = null;
      syncMicrophone();
      // Canceled, incomplete, failed and superseded responses carry no right
      // to execute even if their partial output contains a function call.
      if (response.status !== 'completed' || !permissionActive(permission)) {
        if (/quota|rate_limit|authentication|invalid_api_key|permission/i.test(response.status_details?.error?.code || '')) {
          automaticBlocked = true;
          stop('Voice access is unavailable. Ask the operator to check the OpenAI account, then tap Enable voice.', {preservePreference: true, tone: 'error', needsAction: true});
          return;
        }
        if (response.status === 'completed') acknowledgeInterruptedCalls(calls, permission);
        if (response.status === 'failed' && permissionActive(permission)) status('The voice response failed. Please try again or use the assistance form.', 'error');
        if (response.status === 'incomplete' && permissionActive(permission)) status('The reply reached its limit. Please ask a shorter question or ask BusGuard to continue.', 'error');
        flushPendingTurn(); return;
      }
      const messages = [];
      for (const item of Array.isArray(response.output) ? response.output : []) {
        if (item.type !== 'message' || !Array.isArray(item.content)) continue;
        const text = item.content.map(part => typeof part.transcript === 'string' ? part.transcript : typeof part.text === 'string' ? part.text : '').filter(Boolean).join('\n');
        if (text) messages.push({type: 'message', role: 'assistant', content: [{type: 'output_text', text}]});
      }
      // Generation can finish long before playback. Only retain a spoken reply
      // as heard after playback completes; an interruption drops this text while
      // keeping the authoritative receipts for actions already performed.
      if (audioPending) pendingSpokenContext = {context: permission.context, messages};
      else if (!audioInterrupted) permission.context.push(...messages);
      if (calls.length) {
        if (!permission) { status('Say “Hey BusGuard” or tap Talk now before making a request.', 'listening'); return; }
        toolsBusy = true;
        try {
          for (const item of calls) {
            if (instance !== generation || !permissionActive(permission)) break;
            if (executed.has(item.call_id)) continue;
            executed.add(item.call_id);
            if (executed.size > 100) { scheduleReconnect('Refreshing your voice conversation…', 0); return; }
            let result;
            permission.context.push({type: 'function_call', call_id: item.call_id, name: item.name, arguments: item.arguments || '{}'});
            try { result = await invoke(item, permission); }
            catch (error) { result = {ok: false, message: error.message || 'The bus has not confirmed that action.'}; }
            if (instance !== generation) return;
            // A server action already sent cannot be undone by an interruption.
            // Keep its actual acknowledgement in history, but never continue
            // the interrupted turn or run its remaining calls.
            // This call belongs to the out-of-band response. Its receipt must
            // go into that explicit context, never the default audio conversation.
            permission.context.push({type: 'function_call_output', call_id: item.call_id, output: JSON.stringify(result)});
          }
        } finally {
          if (instance === generation) {
            toolsBusy = false;
            if (!permissionActive(permission)) acknowledgeInterruptedCalls(calls, permission);
            if (pendingTurn) flushPendingTurn();
            else if (permissionActive(permission)) createResponse(permission);
            syncMicrophone();
          }
        }
      } else showListening();
    }
    if (event.type === 'error') {
      // Ignore cancellation races; avoid rendering upstream diagnostics or private request content.
      if (!['response_cancel_not_active', 'conversation_already_has_active_response'].includes(event.error?.code)) {
        if (/quota|rate_limit|authentication|invalid_api_key|permission/i.test(event.error?.code || '')) {
          automaticBlocked = true;
          stop('Voice access is unavailable. Ask the operator to check the OpenAI account, then tap Enable voice.', {preservePreference: true, tone: 'error', needsAction: true});
          return;
        }
        interrupt(); responding = false; responsePermission = activeResponseId = null;
        syncMicrophone();
        status('The voice service could not complete that turn. Please try again.', 'error');
      }
    }
  }
  async function start({automatic = false} = {}) {
    if (disposed || pageAway || document.hidden || starting || sessionPending) return;
    if (connected) {
      if (!automatic) void playReplyAudio();
      return;
    }
    if (!automatic) { rememberListening(true); automaticBlocked = false; retryCount = 0; }
    if (!config?.enabled || !window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
      await refresh(); return;
    }
    clearTimeout(retryTimer); retryTimer = null;
    starting = true; const instance = ++generation; controls();
    status(automatic ? 'Resuming voice listening…' : 'Allow microphone access to enable listening.', 'connecting');
    try {
      await closing;
      if (instance !== generation || disposed || document.hidden) return;
      const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      if (instance !== generation) { stream.getTracks().forEach(track => track.stop()); return; }
      microphone = stream; permissionGranted = true;
      microphone.getTracks().forEach(track => { track.enabled = !announcementActive; });
      const connection = new RTCPeerConnection(); pc = connection;
      audio = new Audio(); audio.autoplay = true; audio.muted = announcementActive;
      connection.ontrack = event => {
        if (instance !== generation || !audio) return;
        audio.srcObject = event.streams[0];
        void playReplyAudio();
      };
      stream.getTracks().forEach(track => connection.addTrack(track, stream));
      channel = connection.createDataChannel('oai-events');
      channel.onmessage = message => {
        const failed = () => { if (instance === generation) status('A voice command could not be completed. Please use the form.', 'error'); };
        try {
          const event = JSON.parse(message.data);
          if (event.type === 'response.done') toolQueue = toolQueue.then(() => handle(event, instance)).catch(failed);
          else void handle(event, instance).catch(failed);
        }
        catch { /* Ignore malformed data; it cannot execute a passenger action. */ }
      };
      channel.onclose = () => { if (instance === generation) scheduleReconnect(); };
      channel.onerror = () => { if (instance === generation) scheduleReconnect(); };
      channel.onopen = () => {
        if (instance !== generation) return;
        connected = true; starting = false; connectedAt = Date.now();
        const lifetime = Math.max(30, Math.min(3300, config.max_session_seconds || 3300));
        sessionUntil = connectedAt + (lifetime - Math.min(30, lifetime / 10)) * 1000;
        wakeGate.reset(); acceptedContext = []; controls(); showStandby();
        timer = setInterval(() => {
          if (Date.now() >= sessionUntil && !audioLease && !responding && !toolsBusy) {
            scheduleReconnect('Keeping your voice connection ready…', 0); return;
          }
          expireConversation();
        }, 500);
      };
      connection.onconnectionstatechange = () => {
        if (instance === generation && ['failed', 'disconnected', 'closed'].includes(connection.connectionState)) scheduleReconnect();
      };
      const offer = await connection.createOffer(); await connection.setLocalDescription(offer);
      await new Promise((resolve, reject) => {
        if (connection.iceGatheringState === 'complete') { resolve(); return; }
        const timeout = setTimeout(resolve, 2500);
        connection.addEventListener('icegatheringstatechange', () => {
          if (connection.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
        });
      });
      if (instance !== generation) return;
      status('Connecting to BusGuard voice…', 'connecting');
      const sessionAbort = new AbortController(); abort = sessionAbort; sessionPending = true; controls();
      const timeout = setTimeout(() => sessionAbort.abort(), 25000);
      let result;
      try {
        const response = await fetch('/api/voice/session', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({sdp: connection.localDescription.sdp, voice: el('choice').value, client_id: clientId}), signal: sessionAbort.signal});
        result = await response.json();
        if (!response.ok) {
          const error = new Error(typeof result.detail === 'string' ? result.detail : 'The voice session could not start.');
          error.status = response.status; throw error;
        }
        if (instance !== generation) { if (result.session_id) await closeSession(result.session_id); return; }
      } finally {
        clearTimeout(timeout); sessionPending = false;
        if (abort === sessionAbort) abort = null;
        controls();
        if (instance !== generation && !connected && !starting && desired && !disposed && !document.hidden) void autoResume();
      }
      sessionHandle = result.session_id || null;
      if (typeof result.sdp !== 'string' || !result.sdp.startsWith('v=')) throw new Error('The voice service returned an invalid connection.');
      await connection.setRemoteDescription({type: 'answer', sdp: result.sdp});
      setTimeout(() => { if (instance === generation && !connected) scheduleReconnect('Voice connection timed out. Reconnecting…'); }, 12000);
    } catch (error) {
      if (instance !== generation) return;
      const needsAction = ['NotAllowedError', 'NotFoundError', 'NotReadableError'].includes(error.name)
        || [400, 401, 402, 403, 429].includes(error.status)
        || /quota|billing|credit|API key|authentication/i.test(error.message || '');
      if (needsAction) {
        automaticBlocked = true;
        if (error.name === 'NotAllowedError') permissionGranted = false;
        stop(error.name === 'NotAllowedError' ? 'Microphone access was not granted. Allow it in this site’s browser permissions, then tap Enable voice.'
          : error.name === 'NotFoundError' ? 'No microphone was found on this device.'
          : error.message || 'Voice needs attention before it can connect.', {preservePreference: true, tone: 'error', needsAction: true});
      } else scheduleReconnect(error.message || 'Voice could not connect. Reconnecting…');
    }
  }
  const hidden = () => {
    if (document.hidden) {
      if (connected || starting || retryTimer) stop('Listening is paused while this app is in the background.', {preservePreference: true, tone: 'paused'});
    } else void autoResume();
  };
  const setupPoll = setInterval(() => {
    if (!document.hidden && !config?.enabled && !connected && !starting) void refresh();
  }, 5000);
  const unload = () => { disposed = true; clearInterval(setupPoll); stop('Listening stopped because this page was closed.', {preservePreference: true}); guide.dispose(); };
  const pageHide = () => { pageAway = true; stop('Listening is paused while this page is closed.', {preservePreference: true, tone: 'paused'}); guide.cancel(); };
  const pageShow = () => { pageAway = false; void autoResume(); };
  el('start').addEventListener('click', () => start());
  el('stop').addEventListener('click', () => stop());
  el('retry').addEventListener('click', refresh);
  el('choice').addEventListener('change', () => { voiceChoiceEdited = true; });
  el('preview').addEventListener('click', async () => {
    const result = await guide.preview(el('choice').value);
    if (result?.preparing) status('Preparing this voice preview. Tap “Hear this voice” again in a few seconds.');
    else if (result?.unavailable) status('The OpenAI voice preview is unavailable. Configure voice on the host, or try again when connected.', 'setup');
  });
  el('talk').addEventListener('click', () => {
    if (playbackBlocked) { void playReplyAudio(); return; }
    if (listeningBlocked()) return;
    interrupt(); wakeGate.standby(); acceptedContext = []; wakeGate.wake(); audio?.play().catch(() => {});
    status('Listening. Tell BusGuard what you need.', 'active');
  });
  document.addEventListener('visibilitychange', hidden);
  window.addEventListener('pagehide', pageHide);
  window.addEventListener('pageshow', pageShow);
  window.addEventListener('busguard:urgent-guidance', interrupt);
  const unsubscribeAudio = audioTurns.subscribe(({kind}) => {
    const wasActive = announcementActive;
    announcementActive = kind === 'announcement';
    if (audio) audio.muted = announcementActive;
    syncMicrophone();
    if (announcementActive && !wasActive) {
      if (latestSpeechId) { ignoredTranscripts.add(latestSpeechId); wakeGate.discard(latestSpeechId); }
      latestSpeechId = null;
      send({type: 'input_audio_buffer.clear'});
      if (connected) status('An announcement is playing. Listening resumes when it finishes.', 'announcing');
    } else if (!announcementActive && wasActive && connected && !listeningBlocked()) {
      expireConversation();
      if (wakeGate.awake) status('I’m listening. Tell BusGuard what you need.', 'active'); else showStandby();
    }
  });
  void refresh();
  return {start, stop, refresh, dispose() { unload(); unsubscribeAudio(); document.removeEventListener('visibilitychange', hidden); window.removeEventListener('pagehide', pageHide); window.removeEventListener('pageshow', pageShow); window.removeEventListener('busguard:urgent-guidance', interrupt); }};
}
