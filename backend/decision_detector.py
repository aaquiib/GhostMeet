"""
Owns turning transcript text into "this needs the user's input" events:
the two-tier trigger (cheap heuristic pass, then LLM confirmation),
scoring against CONFIDENCE_THRESHOLD, and the debounce/coalescing window
that batches detections landing within DEBOUNCE_WINDOW_SECONDS into one
notification instead of one per detection.
"""
