# Incident response

## 1. Detect
An alert, a customer report or an engineer's observation opens an incident. Whoever notices it
first opens the incident channel and pages the on-call engineer.

## 2. Triage
The on-call engineer sets a severity within 15 minutes, using [severity-levels.md](severity-levels.md).
For SEV1 and SEV2, the on-call engineer names an incident commander.

## 3. Escalate
If the on-call engineer cannot mitigate within 30 minutes, they escalate as described in
[escalation.md](escalation.md).

## 4. Mitigate
Stop the harm first: roll back, fail over or switch off the feature. A root-cause fix can wait.

## 5. Resolve
The incident commander declares the incident resolved when the key metric is back to normal for
30 minutes, and posts the final status update.

## 6. Review
Every SEV1 and SEV2 gets a blameless postmortem within five working days, written from
[postmortem-template.md](postmortem-template.md).
