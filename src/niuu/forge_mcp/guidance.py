"""Shared authoring guidance for session and feed notification tools and skills."""

from niuu.domain.notifications import MAX_IMAGE_BYTES, NOTIFICATION_IMAGE_MIME_TYPES
from niuu.question_standard import QUESTION_STANDARD

__all__ = [
    "COMPACT_NOTIFICATION_GUIDANCE",
    "NOTIFICATION_IMAGE_GUIDANCE",
    "NOTIFICATION_TRIGGER_GUIDANCE",
    "QUESTION_GUIDANCE",
    "QUESTION_STANDARD",
]

_BYTES_PER_MB = 1024 * 1024
_IMAGE_TYPE_NAMES = ", ".join(
    sorted(mime.split("/", 1)[1].upper() for mime in NOTIFICATION_IMAGE_MIME_TYPES)
)

# When to notify. Forge no longer raises an automatic notification when a turn ends, so
# the agent's own notifications are the only activity signal the user gets. Keep them
# rare and worth an interruption.
NOTIFICATION_TRIGGER_GUIDANCE = """\
Notify only when the user would want to be interrupted, because they may not be \
watching this conversation. Send one when:
- the task the user asked for is done: kind=milestone, severity=success, title = the \
outcome. Forge sends nothing when a turn ends, so this is the user's "done" signal;
- you need the user: kind=decision when they must choose, kind=attention when they \
must act or unblock (approve, grant access). Both are questions: set question=true and \
send an ask with 2-4 options, one recommended, and a default (see the question standard). \
A choice you already made is not a decision: report it as info;
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


# Question signals (F11b, control-plane cp-api NotifyQuestionFields). The control plane
# opens a Question from question=true and applies about_question by these rules.
QUESTION_GUIDANCE = """\
Questions. Set question=true only when the user must answer (a choice, an approval, \
missing information); it stays in their Needs-you list until it is resolved, and a \
notice that sets question (true or false) is never guessed to be one. The question's \
reference is question:<host>:<notification_id>, where notification_id is what this \
tool returned and host is the host key of the Forge that recorded it (thor, mini-m6). \
Later notices refer to it with about_question={id, action}:
- only the session that asked may remind, replace, resolved or withdrawn its own \
question; from any other session these are rejected and never applied;
- any session filed in the same project, on any host, may relay it (action=relay), \
for example a coordinator passing on a worker's question; relay links, it never copies.
Always name the host: a bare uuid is refused.
Send the question as ask={items:[...]}. Each item has a stable id, kind=question|action \
and text; header and options=[{label, description?, recommended?}] make the choice clear. \
Mark exactly one option recommended=true (the older "<label> (Recommended)" suffix still \
counts). default={option, after} says what happens if the user does not answer: option is \
an option label or "none", after an ISO-8601 timestamp with timezone or a duration from \
now (PT4H, P1D). deadline (ISO-8601 timestamp) is optional. Use unique item ids and \
option labels. Omitted \
multi_select means false; omitted allow_text means true. A question with allow_text=false \
must offer options. Include at most 8 items and \
8 options per item, with at most 32768 UTF-8 bytes of compact ask JSON. Keep these \
items concise instead of hiding a questionnaire in the body. An action asks the user \
to report done/not done; it never authorizes the app to execute anything. Legacy \
question=true without ask still works as one free-text question. Sending an answer \
does not mean the work is resolved: the asking session confirms resolution later.
Example: kind=decision, title "Postgres or SQLite for the cache?", question=true, \
ask={"items":[{"id":"database","kind":"question","text":"Which database for the cache?",\
"options":[{"label":"Postgres","description":"Already deployed","recommended":true},\
{"label":"SQLite"}],"default":{"option":"Postgres","after":"PT4H"}}]}"""


NOTIFICATION_IMAGE_GUIDANCE = f"""\
Attach an image when a picture shows the outcome better than words (a screenshot of \
the finished screen, a chart, a rendered diagram): `image: {{"file_id": "<id>"}}` with \
the file_id present-file returned for the image, or `image: {{"url": "https://..."}}` \
for an image already online, and add `alt`: a short description of what it shows. \
One image per notification ({_IMAGE_TYPE_NAMES}, at most \
{MAX_IMAGE_BYTES // _BYTES_PER_MB} MB). Forge keeps its own copy, so the file may \
change or go away afterwards."""
