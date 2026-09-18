"""
Owns the /ws/demo text-injection path: accepts pre-scripted fake
transcript lines over a WebSocket and feeds them into the same
decision-detection pipeline real ASR output would, so the primary demo
path doesn't depend on live audio or a real Meet call.
"""
