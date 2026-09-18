"""
Owns the streaming ASR connection: opens and manages the AWS Transcribe
Streaming session (Deepgram as the fallback provider, same downstream
shape), feeds it PCM audio frames relayed from the extension, and emits
diarized transcript events for decision_detector to consume.
"""
