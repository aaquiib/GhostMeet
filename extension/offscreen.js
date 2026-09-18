// Runs in the offscreen document. Will own: receiving the captured tab
// MediaStream, building the ScriptProcessorNode pipeline (16kHz, 16-bit
// PCM), routing processed audio to a silent sink (never
// audioContext.destination, to avoid echo/feedback), and streaming PCM
// frames to background.js for relay to the backend.
