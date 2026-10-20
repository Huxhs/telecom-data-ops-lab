# INC-XXXX: <one line: what is broken, for whom>

| Field | Value |
| --- | --- |
| Priority | P1 / P2 / P3 / P4 (see drills/README.md) |
| Status | New / In progress / Resolved / Closed |
| Customer | |
| Order / batch / file | |
| Detected by | alert / customer / colleague / own check |
| Opened | YYYY-MM-DD HH:MM (UTC) |
| SLA due | from `due_at` in `simgen status` |
| Assigned to | |

## Impact
What can't the customer do right now? How many cards, which orders? Is any
wrong or secret data at risk of leaving?

## Evidence
Exact error codes and messages, with where you read them (`.reason` file,
`simgen status`, Splunk search). Quote; don't paraphrase. Never paste keys,
Ki, OPc, PINs or PUKs.

## Timeline (UTC)
- HH:MM detected ...
- HH:MM ...
- HH:MM service restored

## Actions taken
Numbered, with the command or SQL for each. Link any change record.

## Resolution
What restored service, and how you confirmed it (order DELIVERED, QC clean,
customer confirmed receipt).

## Follow-up
- Problem record needed? yes / no, why
- Customer told: who, when, what
