// Service worker. Will own: opening the side panel, starting/stopping
// tabCapture on the active Meet tab, creating the offscreen document,
// relaying audio-pipeline messages between offscreen.js and the
// backend, and detecting the Meet tab closing (the second half of the
// "Stop Ghost" condition).
