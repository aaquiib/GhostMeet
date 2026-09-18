// Runs in the offscreen document. Owns the actual audio pipeline:
// getUserMedia on the tab-capture stream, the Web Audio graph (mono
// downmix -> ScriptProcessorNode -> silent sink, never
// audioContext.destination — that would play the meeting audio out
// loud a second time), PCM16 encoding of each buffer, and the
// WebSocket relay to the backend, including bounded reconnection. Also
// owns sending the session_init identity handshake as the first
// message on every (re)connection, and relaying speaker_override
// submissions as control messages on the same socket.

import {
  START_CAPTURE,
  STOP_CAPTURE,
  CAPTURE_STARTED,
  CAPTURE_STOPPED,
  CAPTURE_ERROR,
  CONNECTION_STATUS,
  SPEAKER_OVERRIDE,
} from './messages.js';

const DEFAULT_BACKEND_URL = 'ws://localhost:8000/ws/transcribe';
const SAMPLE_RATE = 16000;
const BUFFER_SIZE = 4096;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 3000;

// Holds every live piece of the current capture session, or null when
// idle. Reassigned wholesale on start/stop rather than mutated field by
// field piecemeal, so there's never a "half-torn-down" pipeline lying
// around for a stray event to touch.
let pipeline = null;

function broadcast(message) {
  chrome.runtime.sendMessage({ ...message, target: 'sidepanel' });
}

function floatTo16BitPCM(input) {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const clamped = Math.max(-1, Math.min(1, input[i]));
    output[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return output;
}

async function getBackendUrl() {
  const { backendUrl } = await chrome.storage.local.get('backendUrl');
  return backendUrl || DEFAULT_BACKEND_URL;
}

function connectWebSocket(backendUrl) {
  const ws = new WebSocket(backendUrl);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    if (!pipeline) return;
    pipeline.retryCount = 0;
    // Must be the very first thing sent on this socket, before any
    // audio bytes — onaudioprocess only sends once readyState is OPEN,
    // and that can't happen before this synchronous onopen body (which
    // just made it OPEN) finishes running, so ordering is guaranteed.
    // Sent on every (re)connection, not just the first, since the
    // backend expects it as the first frame of every new connection.
    ws.send(
      JSON.stringify({
        type: 'session_init',
        watched_user_name_variants: pipeline.watchedUserNameVariants,
        slack_target: pipeline.slackTarget,
      })
    );
    broadcast({ type: CONNECTION_STATUS, status: 'connected' });
  };

  ws.onclose = () => {
    if (!pipeline || pipeline.deliberateClose) return;

    if (pipeline.retryCount >= MAX_RETRIES) {
      broadcast({ type: CAPTURE_ERROR, message: 'Connection lost' });
      teardownPipeline();
      return;
    }

    pipeline.retryCount += 1;
    broadcast({ type: CONNECTION_STATUS, status: 'reconnecting' });
    setTimeout(() => {
      if (!pipeline || pipeline.deliberateClose) return;
      pipeline.ws = connectWebSocket(pipeline.backendUrl);
    }, RETRY_DELAY_MS);
  };

  return ws;
}

async function startCapture(streamId, watchedUserNameVariants, slackTarget) {
  const backendUrl = await getBackendUrl();

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: 'tab',
        chromeMediaSourceId: streamId,
      },
    },
    video: false,
  });

  const audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
  const source = audioContext.createMediaStreamSource(stream);
  // Force mono downmix explicitly rather than relying on default
  // Web Audio channel-interpretation behavior.
  source.channelCount = 1;
  source.channelCountMode = 'explicit';

  const processor = audioContext.createScriptProcessor(BUFFER_SIZE, 1, 1);
  // Silent sink: processed audio is routed here, never to
  // audioContext.destination, so the meeting audio never plays out
  // loud a second time.
  const sink = audioContext.createMediaStreamDestination();

  pipeline = {
    stream,
    audioContext,
    source,
    processor,
    sink,
    ws: null,
    deliberateClose: false,
    retryCount: 0,
    backendUrl,
    watchedUserNameVariants: watchedUserNameVariants ?? [],
    slackTarget: slackTarget ?? null,
  };

  processor.onaudioprocess = (event) => {
    if (!pipeline || !pipeline.ws || pipeline.ws.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    const pcm16 = floatTo16BitPCM(input);
    pipeline.ws.send(pcm16.buffer);
  };

  source.connect(processor);
  processor.connect(sink);

  broadcast({ type: CONNECTION_STATUS, status: 'connecting' });
  pipeline.ws = connectWebSocket(backendUrl);

  broadcast({ type: CAPTURE_STARTED });
}

function teardownPipeline() {
  if (!pipeline) return;

  pipeline.deliberateClose = true;

  pipeline.processor.onaudioprocess = null;
  pipeline.processor.disconnect();
  pipeline.source.disconnect();
  pipeline.sink.disconnect();
  pipeline.audioContext.close();
  pipeline.stream.getTracks().forEach((track) => track.stop());
  if (pipeline.ws) pipeline.ws.close();

  pipeline = null;
}

function stopCapture() {
  if (!pipeline) return;
  teardownPipeline();
  broadcast({ type: CAPTURE_STOPPED });
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.target !== 'offscreen') return;

  switch (message.type) {
    case START_CAPTURE:
      startCapture(message.streamId, message.watchedUserNameVariants, message.slackTarget).catch(
        (error) => {
          broadcast({ type: CAPTURE_ERROR, message: error.message || 'Failed to start capture' });
        }
      );
      break;
    case STOP_CAPTURE:
      stopCapture();
      break;
    case SPEAKER_OVERRIDE:
      if (pipeline && pipeline.ws && pipeline.ws.readyState === WebSocket.OPEN) {
        pipeline.ws.send(
          JSON.stringify({ type: 'speaker_override', label: message.label, name: message.name })
        );
      }
      break;
    default:
      break;
  }
});
