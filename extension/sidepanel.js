// Side panel logic. Owns: the Start/Stop controls, and rendering
// CAPTURE_STARTED / CAPTURE_STOPPED / CAPTURE_ERROR / CONNECTION_STATUS
// as plain text in the status area — the seed of the "Ghost is
// listening" indicator that Phase 6 will style properly.

import {
  START_CAPTURE,
  STOP_CAPTURE,
  CAPTURE_STARTED,
  CAPTURE_STOPPED,
  CAPTURE_ERROR,
  CONNECTION_STATUS,
} from './messages.js';

const SESSION_STATE_KEY = 'ghostSession';

const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusText = document.getElementById('status-text');

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

restoreState();
