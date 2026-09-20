// Side panel logic. Owns: the one-time identity setup screen (gates
// Start until a name + Slack target are saved), the Start/Stop
// controls, the "Ghost is listening" consent indicator, the live/
// historical decision list, the triggered/answered expanded view,
// search (P2), and the live-chat-style transcript feed.
//
// The transcript feed mirrors a YouTube-live-chat layout: newest line
// at the bottom, auto-scrolling as long as the user hasn't scrolled up
// to read history (see TRANSCRIPT_STICKY_BOTTOM_PX), and any line that
// triggered a Slack notification is highlighted red — matched
// client-side against each notified decision's mention_quote (see
// isTranscriptLineTriggered), not written by the backend, so the
// highlight is correct regardless of whether the transcript line or
// the decision push arrives first.
//
// Everything after setup is driven by one `panelState` object and one
// `render(state)` function: every message handler and user interaction
// updates panelState then calls render — no direct DOM mutation
// scattered through handlers. Decision/transcript text/speaker/context
// is always inserted via textContent, never innerHTML — it originates
// from meeting transcripts and LLM output and must be treated as
// untrusted.

import {
  START_CAPTURE,
  STOP_CAPTURE,
  CAPTURE_STARTED,
  CAPTURE_STOPPED,
  CAPTURE_ERROR,
  CONNECTION_STATUS,
  SPEAKER_OVERRIDE,
  SESSION_ID_ASSIGNED,
  DECISION_BATCH,
  DECISION_STATUS_UPDATE,
  TRANSCRIPT_EVENT,
} from './messages.js';

const SESSION_STATE_KEY = 'ghostSession';
const IDENTITY_STORAGE_KEY = 'ghostIdentity';
// Distinct from background.js's `ghostSession.meetingSessionId` — that
// one is generated client-side and never reaches the backend as the
// real session id (offscreen.js doesn't pass it on connect). This key
// holds the id the backend itself confirmed (SESSION_ID_ASSIGNED), the
// only id GET /decisions and /search can be scoped by.
const PANEL_SESSION_ID_KEY = 'ghostPanelSessionId';
const DEFAULT_BACKEND_WS_URL = 'ws://localhost:8000/ws/transcribe';
const SEARCH_DEBOUNCE_MS = 300;
const ACTIVE_VIEW_STATUSES = new Set(['approved', 'answered_live']);
// Rolling window kept in memory/DOM during a live session — older
// lines stay durable in Neon (transcript_store.py) and are still
// returned by GET /transcript, just not all held in the live feed at
// once. Keeps a very long meeting from growing the DOM unboundedly.
const TRANSCRIPT_LINE_CAP = 300;
// Word-overlap threshold for matching a transcript line against a
// decision's mention_quote — mirrors decision_detector.py's own
// _DEDUP_OVERLAP_THRESHOLD (0.6) on the backend, loosened slightly
// here since a transcript line and a freshly-drafted mention_quote
// don't always segment identically (the LLM copies "verbatim" but ASR
// line boundaries don't always match sentence boundaries exactly).
const TRANSCRIPT_MATCH_THRESHOLD = 0.5;
// Auto-scroll only kicks back in once the user is back within this
// many pixels of the bottom — otherwise scrolling up to read history
// would keep getting yanked back down by new messages.
const TRANSCRIPT_STICKY_BOTTOM_PX = 48;

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

const listeningIndicator = document.getElementById('listening-indicator');
const meetingMeta = document.getElementById('meeting-meta');

const activeDecisionEl = document.getElementById('active-decision');
const backendUnreachableEl = document.getElementById('backend-unreachable');
const retryBtn = document.getElementById('retry-btn');
const searchInput = document.getElementById('search-input');
const decisionListEl = document.getElementById('decision-list');
const decisionListEmptyEl = document.getElementById('decision-list-empty');

const transcriptFeedEl = document.getElementById('transcript-feed');
const transcriptFeedEmptyEl = document.getElementById('transcript-feed-empty');
const transcriptJumpBtn = document.getElementById('transcript-jump-latest');

// --- panelState -------------------------------------------------------

let panelState = {
  captureStatus: 'idle', // 'idle' | 'connecting' | 'capturing' | 'reconnecting' | 'error'
  captureStartedAt: null,
  sessionId: null,
  decisions: [],
  activeDecisionId: null,
  searchQuery: '',
  searchResults: null, // null = showing full list, array = showing search results
  backendReachable: null, // null = not yet checked
  transcriptLines: [], // {id, speaker, text, timestamp, confidence}, oldest first
  transcriptStickToBottom: true,
};

function upsertDecision(state, decision) {
  if (!decision || !decision.id) return state;
  const idx = state.decisions.findIndex((d) => d.id === decision.id);
  if (idx === -1) {
    state.decisions = [...state.decisions, decision];
  } else {
    state.decisions = [
      ...state.decisions.slice(0, idx),
      { ...state.decisions[idx], ...decision },
      ...state.decisions.slice(idx + 1),
    ];
  }
  return state;
}

// Append-only (transcript lines are never edited after the fact, only
// appended) but still deduped by id — a panel reopened mid-session can
// have GET /transcript's hydration and the resumed live stream briefly
// overlap on the same lines (see transcript_store.py's docstring).
// Keeps insertion order and caps memory/DOM growth for a long meeting.
function appendTranscriptLine(state, line) {
  if (!line || !line.id) return state;
  if (state.transcriptLines.some((l) => l.id === line.id)) return state;
  const next = [...state.transcriptLines, line];
  state.transcriptLines =
    next.length > TRANSCRIPT_LINE_CAP ? next.slice(next.length - TRANSCRIPT_LINE_CAP) : next;
  return state;
}

// --- transcript/decision correlation ------------------------------------

function wordOverlap(a, b) {
  const wordsA = new Set((a || '').toLowerCase().match(/\w+/g) || []);
  const wordsB = new Set((b || '').toLowerCase().match(/\w+/g) || []);
  if (wordsA.size === 0 || wordsB.size === 0) return 0;
  let intersection = 0;
  for (const word of wordsA) {
    if (wordsB.has(word)) intersection += 1;
  }
  return intersection / (wordsA.size + wordsB.size - intersection);
}

// True if `line` is the transcript line a notified decision's
// mention_quote was drawn from — mirrors decision_detector.py's own
// text-similarity approach (word-overlap over an exact match) since
// mention_quote is meant to be verbatim but ASR line boundaries and a
// freshly-drafted quote don't always segment identically.
function isTranscriptLineTriggered(line, decisions) {
  const text = (line.text || '').toLowerCase();
  for (const decision of decisions) {
    const quote = (decision.mention_quote || '').toLowerCase();
    if (!quote) continue;
    if (text.includes(quote) || quote.includes(text)) return true;
    if (wordOverlap(line.text, decision.mention_quote) >= TRANSCRIPT_MATCH_THRESHOLD) return true;
  }
  return false;
}

// --- rendering ----------------------------------------------------------

function statusLabel(status) {
  switch (status) {
    case 'pending':
      return 'Pending';
    case 'approved':
      return 'Read';
    case 'rejected':
      return 'Rejected';
    case 'denied_by_policy':
      return 'Denied by policy';
    case 'answered_live':
      return 'Answered live';
    default:
      return status || 'Unknown';
  }
}

function buildDecisionCard(decision) {
  const card = document.createElement('div');
  card.className = `decision-card status-${decision.status || 'pending'}`;

  const meta = document.createElement('div');
  meta.className = 'decision-card-meta';

  const speaker = document.createElement('span');
  speaker.className = 'decision-card-speaker';
  speaker.textContent = decision.speaker || 'Unknown speaker';

  const badge = document.createElement('span');
  badge.className = `status-badge status-${decision.status || 'pending'}`;
  badge.textContent = statusLabel(decision.status);

  meta.appendChild(speaker);
  meta.appendChild(badge);

  const timestampEl = document.createElement('div');
  timestampEl.className = 'decision-card-meta';
  timestampEl.textContent = decision.timestamp ? new Date(decision.timestamp).toLocaleTimeString() : '';

  const text = document.createElement('div');
  text.className = 'decision-card-text';
  text.textContent = decision.decision_text || '';

  card.appendChild(meta);
  card.appendChild(timestampEl);
  card.appendChild(text);
  return card;
}

function renderDecisionList(state) {
  decisionListEl.innerHTML = '';

  if (state.backendReachable === false) {
    decisionListEl.classList.add('hidden');
    decisionListEmptyEl.classList.add('hidden');
    return;
  }

  const source = state.searchResults !== null ? state.searchResults : state.decisions;
  const sorted = [...source].sort((a, b) => {
    const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
    const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
    return tb - ta;
  });

  if (sorted.length === 0) {
    decisionListEl.classList.add('hidden');
    decisionListEmptyEl.classList.remove('hidden');
    decisionListEmptyEl.textContent =
      state.searchResults !== null ? 'No decisions match your search.' : 'No decisions yet.';
    return;
  }

  decisionListEl.classList.remove('hidden');
  decisionListEmptyEl.classList.add('hidden');
  for (const decision of sorted) {
    decisionListEl.appendChild(buildDecisionCard(decision));
  }
}

// --- Transcript feed (live-chat style) -----------------------------------

// Tracks which line ids have already played their entrance animation
// — renderTranscriptFeed rebuilds every line on every render (needed
// so a later decision can retroactively highlight an earlier line),
// so without this every already-visible line would replay the
// slide-in animation on every single new message, which would look
// like the whole feed flickering rather than one new line arriving.
// Presentational only, not real app state, so it lives outside
// panelState.
let animatedLineIds = new Set();

function buildTranscriptLine(line, triggered, isNew) {
  const el = document.createElement('div');
  el.className = `transcript-line${triggered ? ' triggered' : ''}${isNew ? ' transcript-line-enter' : ''}`;

  const meta = document.createElement('div');
  meta.className = 'transcript-line-meta';

  const speaker = document.createElement('span');
  speaker.className = 'transcript-line-speaker';
  speaker.textContent = line.speaker || 'Unknown speaker';

  const timestampEl = document.createElement('span');
  timestampEl.textContent = line.timestamp ? new Date(line.timestamp).toLocaleTimeString() : '';

  meta.appendChild(speaker);
  meta.appendChild(timestampEl);

  const text = document.createElement('div');
  text.className = 'transcript-line-text';
  // Never innerHTML — this is raw meeting-transcript text, untrusted
  // the same way decision text is (see the file header note).
  text.textContent = line.text || '';

  el.appendChild(meta);
  el.appendChild(text);
  return el;
}

function isNearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= TRANSCRIPT_STICKY_BOTTOM_PX;
}

function renderTranscriptFeed(state) {
  const lines = state.transcriptLines;

  if (lines.length === 0) {
    transcriptFeedEl.classList.add('hidden');
    transcriptFeedEmptyEl.classList.remove('hidden');
    transcriptJumpBtn.classList.add('hidden');
    return;
  }

  transcriptFeedEl.classList.remove('hidden');
  transcriptFeedEmptyEl.classList.add('hidden');

  // Only decisions that actually reached notification have a real
  // mention_quote worth matching against — checking every stored
  // decision (including ones the backend never notified on) would
  // just add noise/false positives from a low-signal decision_text
  // summary instead of the verbatim quote.
  const notifiedDecisions = state.decisions.filter((d) => d.mention_quote);

  // Rebuilt from scratch each render (same approach renderDecisionList
  // already uses) — the line counts here are small enough (capped at
  // TRANSCRIPT_LINE_CAP) that this is cheap, and it keeps triggered-
  // highlighting correct regardless of whether transcript lines or
  // decisions arrived first.
  transcriptFeedEl.innerHTML = '';
  for (const line of lines) {
    const triggered = isTranscriptLineTriggered(line, notifiedDecisions);
    const isNew = !animatedLineIds.has(line.id);
    transcriptFeedEl.appendChild(buildTranscriptLine(line, triggered, isNew));
  }
  animatedLineIds = new Set(lines.map((l) => l.id));

  if (state.transcriptStickToBottom) {
    transcriptFeedEl.scrollTop = transcriptFeedEl.scrollHeight;
    transcriptJumpBtn.classList.add('hidden');
  } else {
    transcriptJumpBtn.classList.remove('hidden');
  }
}

function renderActiveDecision(state) {
  const active = state.activeDecisionId
    ? state.decisions.find((d) => d.id === state.activeDecisionId)
    : null;

  if (!active || !ACTIVE_VIEW_STATUSES.has(active.status)) {
    activeDecisionEl.classList.add('hidden');
    activeDecisionEl.innerHTML = '';
    return;
  }

  activeDecisionEl.classList.remove('hidden');
  activeDecisionEl.innerHTML = '';

  const label = document.createElement('div');
  label.className = 'active-decision-label';
  label.textContent = active.status === 'answered_live' ? 'Answered live' : 'Triggered';

  const question = document.createElement('div');
  question.className = 'active-decision-question';
  question.textContent = active.decision_text || '';

  const answer = document.createElement('div');
  answer.className = 'active-decision-answer';
  answer.textContent = active.drafted_answer || 'No drafted answer available.';

  const confirmation = document.createElement('div');
  confirmation.className = 'active-decision-confirmation';
  confirmation.textContent =
    active.status === 'answered_live' ? 'Take over live.' : 'Answer sent.';

  activeDecisionEl.appendChild(label);
  activeDecisionEl.appendChild(question);
  activeDecisionEl.appendChild(answer);
  activeDecisionEl.appendChild(confirmation);
}

function renderBackendUnreachable(state) {
  if (state.backendReachable === false) {
    backendUnreachableEl.classList.remove('hidden');
  } else {
    backendUnreachableEl.classList.add('hidden');
  }
}

function renderListeningIndicator(state) {
  const isActive = state.captureStatus === 'capturing' || state.captureStatus === 'reconnecting';
  if (!isActive) {
    listeningIndicator.classList.add('hidden');
    return;
  }
  listeningIndicator.classList.remove('hidden');

  const parts = [];
  if (state.sessionId) parts.push(`Session ${state.sessionId.slice(0, 8)}`);
  if (state.captureStartedAt) {
    parts.push(`since ${new Date(state.captureStartedAt).toLocaleTimeString()}`);
  }
  meetingMeta.textContent = parts.join(' · ');
}

function render(state) {
  renderListeningIndicator(state);
  renderActiveDecision(state);
  renderBackendUnreachable(state);
  renderTranscriptFeed(state);
  renderDecisionList(state);
}

// Tracks whether the user has scrolled up to read history — only then
// does new content stop auto-scrolling (and show the jump button)
// instead of yanking their view back down. Doesn't call render()
// itself; the next state-changing event's own render() picks up the
// updated flag.
transcriptFeedEl.addEventListener('scroll', () => {
  panelState.transcriptStickToBottom = isNearBottom(transcriptFeedEl);
  if (panelState.transcriptStickToBottom) {
    transcriptJumpBtn.classList.add('hidden');
  }
});

transcriptJumpBtn.addEventListener('click', () => {
  panelState.transcriptStickToBottom = true;
  transcriptFeedEl.scrollTop = transcriptFeedEl.scrollHeight;
  transcriptJumpBtn.classList.add('hidden');
});

// --- capture status (start/stop button + status text) -----------------

function showIdleControls() {
  startBtn.classList.remove('hidden');
  stopBtn.classList.add('hidden');
}

function showCapturingControls() {
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

// --- Backend HTTP base (for GET /decisions, GET /search) --------------

async function getBackendHttpBase() {
  const { backendUrl } = await chrome.storage.local.get('backendUrl');
  const wsUrl = backendUrl || DEFAULT_BACKEND_WS_URL;
  try {
    const parsed = new URL(wsUrl);
    const protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
    return `${protocol}//${parsed.host}`;
  } catch {
    return 'http://localhost:8000';
  }
}

// --- Decision hydration -------------------------------------------------

async function hydrateDecisions(sessionId) {
  if (!sessionId) return;
  try {
    const base = await getBackendHttpBase();
    const response = await fetch(
      `${base}/decisions?meeting_id=${encodeURIComponent(sessionId)}`
    );
    if (!response.ok) throw new Error(`GET /decisions failed: ${response.status}`);
    const data = await response.json();
    panelState.decisions = Array.isArray(data.decisions) ? data.decisions : [];
    panelState.backendReachable = true;
  } catch {
    panelState.backendReachable = false;
  }
  render(panelState);
}

// --- Transcript hydration -------------------------------------------------

async function hydrateTranscript(sessionId) {
  if (!sessionId) return;
  try {
    const base = await getBackendHttpBase();
    const response = await fetch(
      `${base}/transcript?meeting_id=${encodeURIComponent(sessionId)}`
    );
    if (!response.ok) throw new Error(`GET /transcript failed: ${response.status}`);
    const data = await response.json();
    const lines = Array.isArray(data.lines) ? data.lines : [];
    // appendTranscriptLine (not a wholesale replace) so any live
    // TRANSCRIPT_EVENT lines that already arrived before this fetch
    // resolved aren't dropped — dedup-by-id makes the overlap a noop.
    for (const line of lines) {
      appendTranscriptLine(panelState, line);
    }
  } catch {
    // A transcript-hydration failure doesn't flip backendReachable —
    // hydrateDecisions already owns that signal, and a fetch here
    // failing alongside a working decisions fetch would be confusing
    // (it's the same backend/network, so decisions hydration already
    // caught a real outage).
  }
  render(panelState);
}

retryBtn.addEventListener('click', () => {
  hydrateDecisions(panelState.sessionId);
  hydrateTranscript(panelState.sessionId);
});

// --- Search (P2) --------------------------------------------------------

let searchDebounceHandle = null;

searchInput.addEventListener('input', () => {
  const query = searchInput.value;
  panelState.searchQuery = query;

  if (searchDebounceHandle) clearTimeout(searchDebounceHandle);

  const trimmed = query.trim();
  if (trimmed.length < 2) {
    // Clearing (or under the 2-char threshold) restores the full list
    // from panelState.decisions without refetching.
    panelState.searchResults = null;
    render(panelState);
    return;
  }

  searchDebounceHandle = setTimeout(() => {
    runSearch(trimmed);
  }, SEARCH_DEBOUNCE_MS);
});

async function runSearch(query) {
  try {
    const base = await getBackendHttpBase();
    const url = `${base}/search?q=${encodeURIComponent(query)}&meeting_id=${encodeURIComponent(
      panelState.sessionId || ''
    )}`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`GET /search failed: ${response.status}`);
    const data = await response.json();
    panelState.searchResults = Array.isArray(data.results) ? data.results : [];
  } catch {
    panelState.searchResults = [];
  }
  render(panelState);
}

// --- Message handling ---------------------------------------------------

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.target !== 'sidepanel') return;

  switch (message.type) {
    case CAPTURE_STARTED:
      panelState.captureStatus = 'capturing';
      panelState.captureStartedAt = Date.now();
      showCapturingControls();
      setStatus('Ghost is listening.');
      render(panelState);
      break;
    case CAPTURE_STOPPED:
      panelState.captureStatus = 'idle';
      panelState.captureStartedAt = null;
      showIdleControls();
      setStatus('Ghost is idle.');
      render(panelState);
      break;
    case CAPTURE_ERROR:
      // The offscreen document that actually threw this closes itself
      // right after broadcasting, so its own console is a near-
      // impossible window to catch — the side panel's console (this
      // one, already open and stable) is where the real stack trace
      // is actually visible for debugging.
      console.error('Capture error:', message.message, message.stack || '(no stack forwarded)');
      panelState.captureStatus = 'error';
      showIdleControls();
      setStatus(message.message || 'Something went wrong.');
      render(panelState);
      break;
    case CONNECTION_STATUS:
      if (message.status === 'connected') {
        panelState.captureStatus = 'capturing';
        setStatus('Ghost is listening.');
      } else if (message.status === 'connecting') {
        panelState.captureStatus = 'connecting';
        setStatus('Ghost is listening. Connecting to backend…');
      } else if (message.status === 'reconnecting') {
        panelState.captureStatus = 'reconnecting';
        setStatus('Ghost is listening. Connection dropped, reconnecting…');
      }
      render(panelState);
      break;
    case SESSION_ID_ASSIGNED: {
      const newSessionId = message.sessionId;
      if (newSessionId && newSessionId !== panelState.sessionId) {
        panelState.sessionId = newSessionId;
        panelState.decisions = [];
        panelState.activeDecisionId = null;
        panelState.searchResults = null;
        panelState.backendReachable = null;
        panelState.transcriptLines = [];
        panelState.transcriptStickToBottom = true;
        chrome.storage.session.set({ [PANEL_SESSION_ID_KEY]: newSessionId });
        render(panelState);
        hydrateDecisions(newSessionId);
        hydrateTranscript(newSessionId);
      }
      break;
    }
    case DECISION_BATCH: {
      // Demo mode (/ws/demo) never sends SESSION_ID_ASSIGNED (the
      // backend only announces a generated id on /ws/transcribe), so
      // pick the meeting id up opportunistically from the first batch
      // if the panel doesn't already know one.
      if (message.meetingId && !panelState.sessionId) {
        panelState.sessionId = message.meetingId;
        chrome.storage.session.set({ [PANEL_SESSION_ID_KEY]: message.meetingId });
      }
      for (const decision of message.decisions || []) {
        upsertDecision(panelState, decision);
      }
      render(panelState);
      break;
    }
    case TRANSCRIPT_EVENT: {
      // Demo mode picks up its meeting id opportunistically from the
      // first DECISION_BATCH (see above) since /ws/demo never sends
      // SESSION_ID_ASSIGNED — transcript lines arrive first, though,
      // so this covers the same case for the transcript feed to show
      // anything before the first decision (if any) ever lands.
      if (message.meetingId && !panelState.sessionId) {
        panelState.sessionId = message.meetingId;
        chrome.storage.session.set({ [PANEL_SESSION_ID_KEY]: message.meetingId });
      }
      appendTranscriptLine(panelState, {
        id: message.id,
        speaker: message.speaker,
        text: message.text,
        timestamp: message.timestamp,
        confidence: message.confidence,
      });
      render(panelState);
      break;
    }
    case DECISION_STATUS_UPDATE: {
      const existing = panelState.decisions.find((d) => d.id === message.decisionId);
      const merged = {
        ...(existing || { id: message.decisionId }),
        status: message.status,
        approved_by: message.approvedBy,
      };
      upsertDecision(panelState, merged);
      if (ACTIVE_VIEW_STATUSES.has(message.status)) {
        panelState.activeDecisionId = merged.id;
      }
      render(panelState);
      break;
    }
    default:
      break;
  }
});

// Restore whatever state background.js already has on open, so
// reopening the panel mid-capture (or after a service-worker restart)
// shows the right controls instead of always defaulting to idle.
async function restoreState() {
  const { [SESSION_STATE_KEY]: state } = await chrome.storage.session.get(SESSION_STATE_KEY);

  if (state && state.status === 'capturing') {
    panelState.captureStatus = 'capturing';
    showCapturingControls();
    setStatus('Ghost is listening.');
  } else if (state && state.status === 'error') {
    panelState.captureStatus = 'error';
    showIdleControls();
    setStatus('Ghost hit an error. Try starting again.');
  } else {
    panelState.captureStatus = 'idle';
    showIdleControls();
    setStatus('Ghost is idle.');
  }

  const { [PANEL_SESSION_ID_KEY]: storedSessionId } = await chrome.storage.session.get(
    PANEL_SESSION_ID_KEY
  );
  if (storedSessionId) {
    panelState.sessionId = storedSessionId;
  }

  render(panelState);

  if (panelState.sessionId) {
    await hydrateDecisions(panelState.sessionId);
    await hydrateTranscript(panelState.sessionId);
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
