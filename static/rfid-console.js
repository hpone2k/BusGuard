import {api} from './core.js?v=network-5';
import {CARD_PROFILES, cardProfile, cardTapMessage, readerConfig, rfidAdmission} from './rfid-core.js?v=rfid-gate-2';

const eventId = () => [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, '0')).join('');
const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
};

export function mountRfidConsole(host, {onTap = () => {}, getOrigin = () => location.origin} = {}) {
  let permitted = false, online = false, loading = false, busy = false, timer = null, generation = 0, rows = new Map();
  let admission = rfidAdmission(null, false);
  host.innerHTML = `<div class="rfid-heading"><div><p class="eyebrow">RFID / PASSENGER PROFILES</p><h3>Four cards. Personal assistance.</h3><p class="section-footnote">Choose what each card requests. The same card boards once and alights on its next accepted tap.</p></div><span class="mini-badge">ESP32 + RC522</span></div>
    <p data-rfid="access" class="section-footnote" role="status">Pair this operator device to manage cards.</p>
    <div data-rfid="private" hidden><div data-rfid="cards" class="rfid-cards"></div>
    <div class="rfid-live"><strong data-rfid="admission" role="status">RFID locked · waiting for the shared controller.</strong><p class="section-footnote">Taps are accepted only while stationary with doors fully open and boarding still active. Closing, departure checks and travel lock the reader until the next stop opens.</p><span class="small-label">LATEST TAP</span><p data-rfid="latest" role="status">Waiting for the reader.</p><p class="section-footnote">Only accepted taps change occupancy. Each accepted tap grants at least 10 seconds from that tap. New taps close at 50 seconds; the last accepted allowance finishes in full. Boarding is refused when full or closed. The 30-second inside-camera check continues independently.</p></div>
    <details class="rfid-setup"><summary>Connect your ESP32 reader</summary><div class="rfid-setup-body"><p>No LED is needed. Sound plays through the bus website or this console with announcements enabled.</p>
    <ol><li>Download the sketch and configuration template. Add your Wi-Fi details to <code>config.h</code>.</li><li>Generate a reader key below and paste the displayed settings into <code>config.h</code>.</li><li>Upload to your ESP32. Keep it on the same Wi-Fi as this server, then remove and tap a card.</li></ol>
    <div class="rfid-downloads"><a href="/api/rfid/firmware/BusGuardRFID.ino" download>Arduino sketch ↗</a><a href="/api/rfid/firmware/config.example.h" download>Configuration template ↗</a><a href="/api/rfid/firmware/README.md" download>Wiring & setup ↗</a></div>
    <p data-rfid="reader" class="section-footnote">Reader is not configured.</p><form data-rfid="reader-form" class="rfid-reader-form"><label>Reader ID<input data-rfid="reader-id" value="busguard-esp32" pattern="[a-zA-Z0-9_-]{1,48}" maxlength="48" required autocomplete="off"></label><button class="button secondary" type="submit">Generate reader key</button></form>
    <p class="section-footnote">Generating a new key replaces the old key for that reader. This key permits card taps only; it is not your OpenAI API key.</p>
    <div data-rfid="credential" class="rfid-credential" hidden><strong>Copy these settings into config.h</strong><textarea data-rfid="config" aria-label="Private RFID reader settings" readonly spellcheck="false" rows="4"></textarea><button data-rfid="hide-key" type="button" class="button secondary">Hide reader key</button><p class="section-footnote">Shown once here. Keep this key private and use a trusted local Wi-Fi network.</p></div></div></details>
    <details class="rfid-keyboard"><summary>USB keyboard reader</summary><p class="section-footnote">If a reader types the UID, focus this field and tap. The registered card profile is used automatically.</p><form data-rfid="keyboard-form" class="input-row"><input data-rfid="uid" aria-label="RFID card UID" placeholder="04:A0:00:01" maxlength="32" autocomplete="off" required><button class="button secondary" type="submit">Submit tap</button></form></details>
    <p data-rfid="message" class="section-footnote" role="status"></p></div>`;
  const $ = key => host.querySelector(`[data-rfid="${key}"]`);
  const message = (text, error = false) => { $('message').textContent = text; $('message').dataset.error = String(error); };
  const request = (path, method, body) => api(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  function disable() {
    for (const control of host.querySelectorAll('input,select,button')) control.disabled = !permitted || !online || busy;
    $('hide-key').disabled = false;
    for (const row of rows.values()) { row.save.disabled ||= !row.dirty; row.tap.disabled ||= !admission.accepting; }
    for (const control of $('keyboard-form').querySelectorAll('input,button')) control.disabled ||= !admission.accepting;
  }
  function customFields(row) {
    row.custom.hidden = row.select.value !== 'custom';
    row.label.required = !row.custom.hidden;
  }
  function addCard(card) {
    const form = element('form', null, 'rfid-card');
    const top = element('div', null, 'rfid-card-top');
    top.append(element('strong', card.name || `Card ${card.id}`));
    const presence = element('span', 'Not aboard', 'rfid-presence'); top.append(presence);
    const uid = element('code', card.uid, 'rfid-uid');
    const label = element('label', 'Passenger type');
    const select = element('select'); select.setAttribute('aria-label', `Card ${card.id} passenger type`);
    select.append(...CARD_PROFILES.map(([value, name]) => Object.assign(element('option', name), {value})));
    label.append(select);
    const custom = element('div', null, 'rfid-custom');
    const customLabel = element('label', 'Custom passenger type');
    const input = Object.assign(element('input'), {maxLength: 60, placeholder: 'e.g. Passenger with a walking aid'});
    customLabel.append(input); custom.append(customLabel);
    const priority = Object.assign(element('input'), {type: 'checkbox'});
    const extra = Object.assign(element('input'), {type: 'checkbox'});
    for (const [checkbox, text] of [[priority, 'Priority seat assistance'], [extra, 'May need extra boarding time']]) {
      const check = element('label', null, 'inline-check'); check.append(checkbox, document.createTextNode(text)); custom.append(check);
    }
    const note = element('p', '', 'rfid-profile-note');
    const actions = element('div', null, 'rfid-card-actions');
    const save = Object.assign(element('button', 'Save profile', 'button secondary'), {type: 'submit'});
    const tap = Object.assign(element('button', 'Play tap', 'rfid-test-tap'), {type: 'button'});
    actions.append(save, tap); form.append(top, uid, label, custom, note, actions); $('cards').append(form);
    const row = {form, presence, select, custom, label: input, priority, extra, note, save, tap, dirty: false, profileKey: null};
    form.oninput = () => { row.dirty = true; customFields(row); row.note.textContent = 'Unsaved changes · save before tapping'; disable(); };
    select.onchange = form.oninput;
    form.onsubmit = async event => {
      event.preventDefault(); if (!permitted || !online || busy) return;
      try {
        const body = cardProfile(select.value, input.value, priority.checked, extra.checked);
        busy = true; const version = ++generation; disable();
        const saved = await request(`/api/rfid/cards/${encodeURIComponent(card.id)}`, 'PUT', body);
        if (!permitted || version !== generation) return;
        row.dirty = false; display(saved); message(`Card ${card.id} profile saved. It will be used on the next boarding tap.`);
      } catch (error) { message(error.message, true); }
      finally { busy = false; disable(); }
    };
    tap.onclick = () => submitTap(card.uid, 'simulation');
    rows.set(card.id, row); return row;
  }
  function display(snapshot) {
    for (const card of snapshot.cards || []) {
      const row = rows.get(card.id) || addCard(card);
      row.presence.textContent = card.onboard ? 'Aboard' : 'Not aboard'; row.presence.dataset.aboard = String(Boolean(card.onboard));
      if (!row.dirty) {
        row.select.value = card.passenger_type; row.label.value = card.custom_label || '';
        row.priority.checked = Boolean(card.priority); row.extra.checked = Boolean(card.extra_time); customFields(row);
        row.note.textContent = card.priority ? 'Priority seat announcement on boarding.'
          : card.extra_time ? 'Extra-time preference · shared boarding limit applies.' : 'Standard boarding.';
      }
    }
    $('latest').textContent = cardTapMessage(snapshot.last_scan);
    $('latest').dataset.denied = String(snapshot.last_scan?.allowed === false);
    $('reader').textContent = snapshot.readers?.length ? snapshot.readers.map(reader => {
      const seen = reader.last_seen_at_ms ? new Date(reader.last_seen_at_ms).toLocaleTimeString() : null;
      return `${reader.reader_id} · ${seen ? `last connected ${seen}` : 'key ready · waiting for device'}`;
    }).join(' · ') : 'No reader key yet. Generate one to connect your ESP32.';
    disable();
  }
  async function refresh() {
    if (!permitted || !online || loading || document.hidden) return;
    loading = true; const version = generation;
    try { const snapshot = await api('/api/rfid/cards'); if (permitted && online && version === generation) display(snapshot); }
    catch (error) { message(`Card settings unavailable: ${error.message}`, true); }
    finally { loading = false; }
  }
  async function submitTap(uid, source = 'keyboard') {
    if (!permitted || !online || busy) return;
    if (!admission.accepting) { message(admission.message); return; }
    busy = true; const version = ++generation; disable(); onTap();
    try {
      const result = await request('/api/rfid/test-tap', 'POST', {uid, event_id: eventId(), source});
      if (!permitted || version !== generation) return;
      message(cardTapMessage(result), result.allowed === false); await refresh();
    } catch (error) { message(error.message, true); }
    finally { busy = false; disable(); }
  }
  $('keyboard-form').onsubmit = event => { event.preventDefault(); const uid = $('uid').value.trim(); if (uid) { $('uid').value = ''; void submitTap(uid); } };
  $('reader-form').onsubmit = async event => {
    event.preventDefault(); if (!permitted || !online || busy) return;
    busy = true; const version = ++generation; disable(); $('credential').hidden = true; $('config').value = '';
    try {
      const readerId = $('reader-id').value.trim();
      const result = await request('/api/rfid/readers', 'POST', {reader_id: readerId, label: 'ESP32 RC522'});
      if (!permitted || !online || version !== generation) return;
      $('config').value = readerConfig({readerId: result.reader_id, token: result.token, origin: getOrigin()});
      $('credential').hidden = false; message('Reader key generated. Copy it to the ESP32 configuration.'); await refresh();
    } catch (error) { message(error.message, true); }
    finally { busy = false; disable(); }
  };
  $('hide-key').onclick = () => { $('credential').hidden = true; $('config').value = ''; };
  return {
    setAccess(canManage, connected, state) {
      const changed = permitted !== Boolean(canManage) || online !== Boolean(connected);
      if (changed) generation++;
      permitted = Boolean(canManage); online = Boolean(connected); $('private').hidden = !permitted;
      admission = rfidAdmission(state, online);
      $('admission').textContent = admission.message;
      $('admission').dataset.open = String(admission.accepting);
      $('access').textContent = !permitted ? 'Pair this operator device to manage cards.'
        : !online ? 'Controller offline. Card changes and test taps are unavailable.' : 'Profiles are saved on this server and shared with your RFID reader.';
      if (!permitted) { $('config').value = ''; $('credential').hidden = true; }
      disable();
      if (changed) {
        clearInterval(timer); timer = null;
        if (permitted && online) { void refresh(); timer = setInterval(refresh, 3000); }
      }
    },
  };
}
