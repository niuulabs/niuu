"""Shared authoring guidance for session and feed notification tools and skills."""

# When to notify. Forge no longer raises an automatic notification when a turn ends, so
# the agent's own notifications are the only activity signal the user gets. Keep them
# rare and worth an interruption.
NOTIFICATION_TRIGGER_GUIDANCE = """\
Notify only when the user would want to be interrupted, because they may not be \
watching this conversation. Send one when:
- the task the user asked for is done: kind=milestone, severity=success, title = the \
outcome. Forge sends nothing when a turn ends, so this is the user's "done" signal;
- you need the user to act: kind=attention when you are blocked or need approval, \
kind=decision when they must choose (give the options and your recommendation);
- something failed that stops the work or needs them: kind=error;
- an important result they would act on before the whole task ends, such as a PR \
ready for review or a deployment finished: kind=milestone.
Do not notify when a turn ends, to answer a chat message, to acknowledge a request, \
for plans, routine steps or progress, or when nothing needs the user. If in doubt, \
do not notify: a few notifications per task, never one per turn."""

COMPACT_NOTIFICATION_GUIDANCE = """\
Activities are compact event markers, not reports. Post one meaningful outcome, \
decision, request or failure per notification.
- Title: carry the key outcome or required action in one plain, specific line \
(aim for at most 72 characters). It must make sense without opening the body.
- Body: optional, one short sentence (aim for at most 160 characters). Add only \
new context or the next action; never repeat the title. Omit it when the title is enough.
- Keep explanations, test inventories, logs, checklists and technical identifiers \
in the conversation or a linked artifact, not in the activity. No multiline \
progress report or pasted final answer. Use `links` for supporting evidence.
- Forge supplies project/session, host, model and time from the session context; \
do not invent that metadata or repeat it in the message.
Examples: title "iOS 2289 is on TestFlight", \
body "Ready to test Activities and project navigation."; \
title "Microphone permission needed", body "Allow microphone access in Settings to continue." \
These are writing targets, not new API length limits; preserve existing records."""
