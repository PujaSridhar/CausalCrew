# On-call Handoff
Date: 2026-09-06
Author: oncall

Overnight notes for the incoming on-call.

- Success rate alert fired at 02:14 and again at 09:40. Rolling 24h
  success dipped below the 99.0% threshold both times.
- Failures are concentrated in document_extract. The dominant
  failure_reason is schema_parse_error.
- Retries are not helping. Normally a retry clears a transient
  failure; these are failing again on the second and third attempt.
- Cost per successful task is up noticeably week over week.

No page was escalated. Leaving this for the weekday team. First
suspicion is the Acme volume ramp, since the timing lines up almost
exactly and their documents are much larger than our median.
