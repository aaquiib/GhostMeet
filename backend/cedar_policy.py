"""
Owns the Cedar authorization check that gates every outbound Slack
notification: loads policies/decisions.cedar via cedarpy, builds the
principal/action/resource/context for a detected decision, and returns
allow/deny. A deny means the decision is logged and nothing is sent.
"""
