// Shared message-type constants for chrome.runtime.sendMessage traffic
// between background.js, offscreen.js, and sidepanel.js. Every message
// sent anywhere in the extension must use one of these as its `type` —
// never a raw string literal — so a typo doesn't silently no-op.

export const START_CAPTURE = 'START_CAPTURE';
export const STOP_CAPTURE = 'STOP_CAPTURE';
export const CAPTURE_STARTED = 'CAPTURE_STARTED';
export const CAPTURE_STOPPED = 'CAPTURE_STOPPED';
export const CAPTURE_ERROR = 'CAPTURE_ERROR';
export const CONNECTION_STATUS = 'CONNECTION_STATUS';
// sidepanel -> background -> offscreen -> backend (as a WS control
// message), the moment a speaker-override is submitted.
export const SPEAKER_OVERRIDE = 'SPEAKER_OVERRIDE';
// offscreen -> sidepanel: relays of backend WS pushes, distinguished by
// the WS message's own "type"/shape rather than reusing that string
// directly, so the sidepanel<->offscreen contract stays independent of
// the backend's wire format.
export const SESSION_ID_ASSIGNED = 'SESSION_ID_ASSIGNED';
export const DECISION_BATCH = 'DECISION_BATCH';
export const DECISION_STATUS_UPDATE = 'DECISION_STATUS_UPDATE';
export const TRANSCRIPT_EVENT = 'TRANSCRIPT_EVENT';
// sidepanel -> background, sent on every panel open: asks background.js
// to reconcile its stored chrome.storage.session status against whether
// a capture pipeline is actually alive (offscreen.hasDocument()) before
// handing it back, rather than the panel trusting the stored flag
// as-is. See background.js's GET_SESSION_STATE handler for why —
// without this, a stale "capturing" flag left behind by an offscreen
// document that died outside the normal stop/error path (e.g. a dev
// extension reload) makes the listening indicator show on next open
// even though Start was never clicked this session.
export const GET_SESSION_STATE = 'GET_SESSION_STATE';
