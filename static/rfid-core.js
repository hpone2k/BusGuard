export const CARD_PROFILES = [
  ['normal', 'Normal passenger'], ['senior', 'Senior resident'],
  ['pregnant', 'Pregnant passenger'], ['custom', 'Custom passenger type'],
];

export function cardProfile(type, label = '', priority = false, extraTime = false) {
  if (!CARD_PROFILES.some(([value]) => value === type)) throw new Error('Choose a passenger type.');
  const custom = type === 'custom';
  label = String(label).trim();
  if (custom && (!label || label.length > 60 || /[<>\u0000-\u001f]/u.test(label)))
    throw new Error('Use a plain-text custom passenger type of 1–60 characters.');
  const needsPriority = type === 'senior' || type === 'pregnant';
  return {passenger_type: type, custom_label: custom ? label : '',
    priority: custom ? Boolean(priority) : needsPriority,
    extra_time: custom ? Boolean(extraTime) : needsPriority};
}

export function cardTapMessage(scan) {
  if (!scan) return 'Ready for a card. Taps appear here when the reader connects.';
  const name = scan.card_id ? `Card ${scan.card_id} · ` : '';
  return name + (scan.message || 'Tap received. Check the controller status.');
}

// Display the server's admission decision; the tap endpoint checks it again.
// Capacity does not lock the reader because an aboard passenger may still exit.
export function rfidAdmission(state, online = true) {
  const vehicle = state?.vehicle;
  if (!online || !vehicle) return {accepting: false, message: 'RFID locked · waiting for the shared controller.'};
  if (vehicle.emergency || vehicle.revalidation_required)
    return {accepting: false, message: 'RFID locked · the simulation needs operator attention.'};
  if (vehicle.motion !== 'stationary') return {accepting: false, message: 'RFID locked · wait for the next stop and open doors.'};
  if (vehicle.doors !== 'open') return {accepting: false, message: 'RFID locked · the doors must be fully open.'};
  if (state.scenario?.enabled !== true || state.scenario?.admission_open !== true)
    return {accepting: false, message: 'RFID locked · boarding has not opened or has ended. Wait for the next boarding window.'};
  return {accepting: true, message: 'RFID ready · doors open for boarding and alighting.'};
}

export function readerConfig({readerId, token, origin}) {
  const url = new URL(origin);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Use the detection console address.');
  if (!/^[a-zA-Z0-9_-]{1,48}$/.test(readerId) || !/^[a-zA-Z0-9_-]{16,256}$/.test(token))
    throw new Error('The reader setup was incomplete. Generate the reader key again.');
  return `Server: ${url.origin}\nReader ID: ${readerId}\nReader key: ${token}`;
}
