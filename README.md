# dsbot

`dsbot` is an auditable, human-in-the-loop data scientist assistant. It triages
mail, drafts responses, analyzes attached data, prepares meeting proposals, and
generates conference reminders. Messages are never sent and calendar events are
never booked by the recurring processing cycle without a human approval.

## Architecture

```text
       Mock / Gmail / Graph connectors
                    |
                    v
         +---------------------+
         | dsbot.orchestrator  |<----- data/KILL stops a cycle
         +---------------------+
           |         |       |
           v         v       v
        Email     Data     Meetings
        triage   pipeline  proposals
           |         |       |
           +---------+-------+
                     |
            Human approval queue
                     |
              SQLite audit log
                     |
          Daily digest / weekly review

 Every LLM request -> dsbot.llm -> local PII redaction -> rate/budget checks
                                       -> FakeLLM or Anthropic
```

Core records and daily LLM usage are stored in SQLite (`data/dsbot.db` by
default). Reports, spreadsheets, digests, approvals, and learning proposals are
written beneath `outputs/` and `data/`. The local reversible PII replacement
map exists only for the duration of one model call; the original text is not
sent in that prompt.

## Setup

Requires Python 3.11 or newer.

```powershell
python -m pip install -e .
python -m pytest
```

Mock mode works offline and requires no model credentials. For Anthropic or an
Anthropic-compatible endpoint such as OpenRouter, provide credentials only
through the environment (never YAML or source control). PowerShell example:

```powershell
$env:ANTHROPIC_BASE_URL = "https://openrouter.ai/api"
$env:OPENROUTER_API_KEY = "<YOUR_API_KEY_HERE>"
$env:ANTHROPIC_MODEL = "nvidia/nemotron-3.5-lightning:free"
```

`ANTHROPIC_MODEL` selects the model and takes precedence over `AGENT_MODEL`.
`OPENROUTER_API_KEY` or, alternatively, `ANTHROPIC_AUTH_TOKEN` supplies bearer
authentication; `ANTHROPIC_API_KEY` remains supported for direct Anthropic use.
When `OPENROUTER_API_KEY` is present, the client posts OpenAI-compatible chat
completions to `https://openrouter.ai/api/v1/chat/completions` (or the
configured `OPENROUTER_BASE_URL` / `ANTHROPIC_BASE_URL`). It uses a 30-second
timeout and retries transient failures twice; classification falls back to
deterministic rules when the provider is unavailable.
Do not paste real credentials into source files, documentation, shell history,
or audit logs. Prefer a managed secret store or a temporary environment variable.

The application also accepts `DSBOT_DB_PATH` for a non-default SQLite file and
`DSBOT_MODE=mock` to force the offline implementation. LLM controls can be
overridden using `DSBOT_LLM_REQUESTS_PER_MINUTE`, `DSBOT_LLM_DAILY_TOKEN_CAP`,
`DSBOT_LLM_DAILY_COST_CAP_USD`, `DSBOT_LLM_COST_PER_MILLION_TOKENS_USD`,
`DSBOT_LLM_MAX_RETRIES`, and `DSBOT_LLM_BACKOFF_BASE_SECONDS`. Set a daily cap
to `0` to disable that cap. Daily token and cost figures are estimates based on
prompt/response text length; they are safety controls, not provider billing
statements.

## CLI

Run commands from the project root after installation. Use `dsbot --help` or
`dsbot <group> --help` for options.

| Command | Purpose |
|---|---|
| `dsbot run-once [--reprocess]` | Run one fetch, triage, drafting, data, meeting, reminder, and digest cycle; already-classified messages are skipped unless explicitly reprocessed. |
| `dsbot start` | Start the scheduler: cycle every five minutes, daily digest at 08:00, weekly review and learning proposal Sunday at 18:00 local time. |
| `dsbot digest` | Write today's digest to `outputs/digest/`. |
| `dsbot email-triage EMAIL.json` | Classify an email file and queue eligible drafts for approval. |
| `dsbot triage` | Triage unread mock mailbox messages. |
| `dsbot process-data SOURCE.json` | Extract, clean, export Excel, generate a narrative report, and queue approval. |
| `dsbot report FILE.csv` | Run the pandas pipeline and create the guarded Excel and DOCX outputs. |
| `dsbot schedule-meeting TITLE ORGANIZER` | Queue a proposed meeting for approval. |
| `dsbot create-conference NAME ORGANIZER START END` | Create and queue a conference proposal. |
| `dsbot list-approvals` | List pending approval requests. |
| `dsbot approve ID` / `dsbot reject ID` | Decide a pending approval. Meeting approvals require an explicit slot selection. |
| `dsbot audit-logs` | Display audit records; `--since ISO_DATETIME` filters by date. |
| `dsbot approvals list` | List SQLite-backed pending approvals. |
| `dsbot approvals show ID` | Display an approval's details. |
| `dsbot approvals approve ID` / `dsbot approvals reject ID` | Approve or reject an item with the approval workflow's required options. |
| `dsbot meetings propose EMAIL.json` | Propose meeting slots from an email and queue a reply for approval. |
| `dsbot meetings brief EVENT_ID` | Generate a preparation brief for a calendar event. |
| `dsbot meetings minutes TRANSCRIPT.txt` | Generate validated meeting minutes. |
| `dsbot conferences remind` | Generate conference deadline reminders; `--file` selects the YAML input. |
| `dsbot feedback mark EMAIL_ID --important` | Store an explicit triage correction. Also supports `--not-important` or `--category`. |
| `dsbot learn propose --kind style\|triage\|meeting` | Generate a reversible learning proposal. |
| `dsbot learn list` | List proposals and their states. |
| `dsbot learn approve PROPOSAL_ID` | Apply a pending proposal after human approval and golden-set checks. |
| `dsbot learn rollback VERSION` | Roll back a previously applied proposal. |

To stop scheduled and one-shot cycles immediately, create an empty file named
`data/KILL`. Remove that file to resume processing. A stopped cycle logs the
kill-switch activation and returns without fetching mail or calling the LLM.

`run-once`, `triage`, and `digest` print whether a live or fake LLM is active
before starting. If the LLM is unavailable or returns invalid classification
JSON twice, deterministic rules classify security threats, VIP urgency, data
requests, meeting requests, and FYI subjects. Suspicious messages are flagged
for review and never drafted. The digest excludes promotional and suspicious
messages from priority email listings and reports suspicious mail separately.

## Learning layer: operation and limits

Learning uses explicit human-confirmed feedback only: email approvals, edits,
rejections, triage corrections, and selected meeting slots. Email content is
never treated as learning feedback. `dsbot learn propose` can propose style,
triage-weight, or meeting-preference changes. Proposals are evaluated against
the golden set, stored as pending/rejected records, and must be explicitly
approved before application. Applied file/settings changes are versioned and
can be reversed with `dsbot learn rollback`.

Learning is constrained: it may adjust few-shot memory, learned reply style,
priority weights, scheduling preferences, and cleaning preferences. It may
never change `dsbot/guardrails.py`, `BLOCKED_ACTIONS`, autonomy level, or
approval requirements. No proposal bypasses human approval or golden-set
checks.

## Replacing mock connectors

The application interfaces are in `dsbot/connectors/base.py`. Keep credentials
in the environment and implement the existing provider interfaces; do not add
send or calendar-booking behavior to the processing cycle.

* Mail: replace/inject `MockMailProvider` from
  `dsbot/connectors/mock_mail.py` with a provider such as
  `dsbot/connectors/gmail_mail.py` or `dsbot/connectors/graph_mail.py`.
  Implement `MailProvider.fetch_unread()` to normalize provider messages to
  `Email`, `MailProvider.mark(email_id, label)` for safe labels, and
  `MailProvider.create_draft(to, subject, body, thread_id, cc)` to create an
  unsent draft. Do not implement auto-send in `run_cycle`.
* Calendar: replace/inject `MockCalendarProvider` from
  `dsbot/connectors/mock_calendar.py` with a Graph-backed provider in
  `dsbot/connectors/graph_calendar.py`. Implement
  `CalendarProvider.get_events(start, end)`, `is_free(start, end)`,
  `find_slots(duration_min, window_start, window_end)`, and
  `create_event(...)`. The only calendar event creation path must remain behind
  a human-approved meeting item and an explicit selected slot.
* Wire the selected implementations into `run_cycle(mail_provider=...,
  calendar_provider=...)` in `dsbot/orchestrator.py`; wire the same factories
  into `dsbot/scheduler.py` for scheduled runs. Keep provider behavior covered
  by offline mock tests before enabling production credentials.

The current `dsbot/connectors/imap_mail.py` and
`dsbot/connectors/graph_calendar.py` are provider stubs, not production-ready
integrations.

## Security checklist for IT review

- [ ] Store API credentials in environment-managed secrets only; rotate them
  through the organization's secret-management process.
- [ ] Confirm PII redaction coverage for the organization's formats and ensure
  no prompt, response, or credential is added to audit logs.
- [ ] Set and validate model request/minute, daily token, and daily cost limits;
  confirm the operating team receives budget-pause alerts.
- [ ] Test `data/KILL` in deployment and confirm the scheduler stops processing.
- [ ] Review `config/settings.yaml` allowlisted domains and test the
  `NEW EXTERNAL RECIPIENT` approval warning.
- [ ] Require a human decision for every outbound email, report release, and
  calendar write; verify the production approval identity and authorization.
- [ ] Restrict filesystem and SQLite access, protect backups, and set retention
  periods for mail, reports, approvals, feedback, and logs.
- [ ] Review audit access, log retention, incident response, and secret
  scrubbing for every connector and operational command.
- [ ] Validate vendor data-processing terms, model-region controls, prompt
  retention, and external connector scopes before enabling production.
- [ ] Run the full offline test suite and a production-like connector staging
  test before deployment.

## Development

Run the full offline suite:

```powershell
python -m pytest
```

Project requirements and non-negotiable operating rules are documented in
[`CLAUDE.md`](CLAUDE.md).
