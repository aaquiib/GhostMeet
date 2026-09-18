// Side panel logic. Owns: the one-time identity setup screen (gates
// Start until a name + Slack target are saved), the Start/Stop
// controls, rendering CAPTURE_STARTED / CAPTURE_STOPPED /
// CAPTURE_ERROR / CONNECTION_STATUS as plain text in the status area
// (the seed of the "Ghost is listening" indicator Phase 6 will style
// properly), and the manual speaker-override input.

import {
  START_CAPTURE,
  STOP_CAPTURE,
  CAPTURE_STARTED,
  CAPTURE_STOPPED,
  CAPTURE_ERROR,
  CONNECTION_STATUS,
  SPEAKER_OVERRIDE,
} from './messages.js';

const SESSION_STATE_KEY = 'ghostSession';
const IDENTITY_STORAGE_KEY = 'ghostIdentity';

const setupScreen = document.getElementById('setup-screen');
const setupNameInput = document.getElementById('setup-name');
const setupNameVariantsInput = document.getElementById('setup-name-variants');
const setupSlackTargetInput = document.getElementById('setup-slack-target');
const setupSaveBtn = document.getElementById('setup-save-btn');
const setupError = document.getElementById('setup-error');

const mainScreen = document.getElementById('main-screen');
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusText = document.getElementById('status-text');

const overrideLabelInput = document.getElementById('override-label');
const overrideNameInput = document.getElementById('override-name');
const overrideApplyBtn = document.getElementById('override-apply-btn');

function showIdle() {
  startBtn.classList.remove('hidden');
  stopBtn.classList.add('hidden');
}

function showCapturing() {
  startBtn.classList.add('hidden');
  stopBtn.classList.remove('hidden');
}

function setStatus(text) {
  statusText.textContent = text;
}

// --- Identity setup -------------------------------------------------

function showSetupScreen() {
  setupScreen.classList.remove('hidden');
  mainScreen.classList.add('hidden');
}

function showMainScreen() {
  setupScreen.classList.add('hidden');
  mainScreen.classList.remove('hidden');
}

setupSaveBtn.addEventListener('click', async () => {
  const name = setupNameInput.value.trim();
  const slackTarget = setupSlackTargetInput.value.trim();
  const extraVariants = setupNameVariantsInput.value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);

  if (!name || !slackTarget) {
    setupError.textContent = 'Both your name and a Slack user ID or email are required.';
    setupError.classList.remove('hidden');
    return;
  }
  setupError.classList.add('hidden');

  // Dedup while preserving order, primary name first — Tier 1's regex
  // (Phase 3) takes this whole list, not just one exact string.
  const nameVariants = [...new Set([name, ...extraVariants])];

  await chrome.storage.local.set({
    [IDENTITY_STORAGE_KEY]: { name, nameVariants, slackTarget },
  });

  showMainScreen();
});

// --- Speaker override -------------------------------------------------

overrideApplyBtn.addEventListener('click', () => {
  const label = overrideLabelInput.value.trim();
  const name = overrideNameInput.value.trim();
  if (!label || !name) return;

  // Sent the moment it's submitted, not held until session end —
  // background.js relays it to offscreen.js, which sends it as a
  // control message on the already-open WebSocket.
  chrome.runtime.sendMessage({ type: SPEAKER_OVERRIDE, target: 'background', label, name });

  overrideLabelInput.value = '';
  overrideNameInput.value = '';
});

// --- Start/Stop + status -------------------------------------------------

startBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: START_CAPTURE, target: 'background' });
  setStatus('Starting…');
});

stopBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: STOP_CAPTURE, target: 'background' });
  setStatus('Stopping…');
});

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.target !== 'sidepanel') return;

  switch (message.type) {
    case CAPTURE_STARTED:
      showCapturing();
      setStatus('Ghost is listening.');
      break;
    case CAPTURE_STOPPED:
      showIdle();
      setStatus('Ghost is idle.');
      break;
    case CAPTURE_ERROR:
      showIdle();
      setStatus(message.message || 'Something went wrong.');
      break;
    case CONNECTION_STATUS:
      if (message.status === 'connected') {
        setStatus('Ghost is listening.');
      } else if (message.status === 'connecting') {
        setStatus('Ghost is listening. Connecting to backend…');
      } else if (message.status === 'reconnecting') {
        setStatus('Ghost is listening. Connection dropped, reconnecting…');
      }
      break;
    default:
      break;
  }
});

// Restore whatever state background.js already has on open, so
// reopening the panel mid-capture (or after a service-worker restart)
// shows the right controls instead of always defaulting to idle.
async function restoreState() {
  const { [SESSION_STATE_KEY]: state } = await chrome.storage.session.get(SESSION_STATE_KEY);
  if (!state) return;

  if (state.status === 'capturing') {
    showCapturing();
    setStatus('Ghost is listening.');
  } else if (state.status === 'error') {
    showIdle();
    setStatus('Ghost hit an error. Try starting again.');
  } else {
    showIdle();
    setStatus('Ghost is idle.');
  }
}

async function init() {
  const { [IDENTITY_STORAGE_KEY]: identity } = await chrome.storage.local.get(IDENTITY_STORAGE_KEY);
  if (identity && identity.name && identity.slackTarget) {
    showMainScreen();
    await restoreState();
  } else {
    showSetupScreen();
  }
}

init();
