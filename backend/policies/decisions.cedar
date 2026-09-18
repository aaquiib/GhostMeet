// Placeholder policy for phase 0. Replace in phase 4 with real rules
// scoping which decisions may trigger a Slack notification.
// Kept as a standalone .cedar file (not a Python string) so it can be
// read and edited independently of application code.

permit (
    principal,
    action == Action::"notify",
    resource
);
