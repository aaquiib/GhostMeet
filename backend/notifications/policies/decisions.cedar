// Decision notification policy — gates every outbound Slack
// notification (cedar_policy.check_decision_policy). Kept as a
// standalone .cedar file (not a Python string) so it can be read and
// edited independently of application code.
//
// principal = User::"<slack target / requires_action_from>"
// action    = Action::"notify"
// resource  = DecisionType::"<classify_decision_type() output>",
//             with a `decision_type` attribute of the same value.
//
// Default is permit; forbid narrows it for decision types sensitive
// enough to withhold from an automatic Slack ping — edit freely.

forbid (
    principal,
    action == Action::"notify",
    resource
) when { resource.decision_type == "hiring" };

permit (
    principal,
    action == Action::"notify",
    resource
);
