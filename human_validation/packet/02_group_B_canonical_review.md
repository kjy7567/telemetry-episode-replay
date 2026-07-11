# Canonical Review Form: Group B

Open this form only after completing the blind authoring form.

## HV-02: Day Mean Lookup

**Canonical initial request:** Operator handoff: "What did the min discharge air temperature setpoint limit on FCU 002 in conference room 010 average out to in BTS_B?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean July 1, 2021."}`

**Goal revisions:** `["Now keep the same signal and site, but give me the previous day.", "Now keep the same site and the same signal, and if I only know it was around 23:59 UTC on June 29, 2021, give me the nearest available reading.", "For June 30, 2021, would you answer or abstain based on data quality?", "Considering both the timestamped reading and the data-quality check we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-04: Point Disambiguation

**Canonical initial request:** Operator handoff: "I need the stream behind the mode state on FCU 082. Which one is it?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["site_id"]`

**Clarification answers:** `{"site_id": "It's in BTS_A."}`

**Goal revisions:** `["Now keep the same site and measurement type, but if the operator meant Fcu 086 instead, which stream should I use?", "Now keep the same site and that signal, and if I only know it was around 00:03 UTC on January 1, 2021, give me the nearest available reading.", "For the week beginning December 28, 2020, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-06: Quality Gate

**Canonical initial request:** Data-quality review request: "Would you trust this signal enough for the weekly trend question about the air flow reading on Exhaust Fan 048 for the week beginning March 22, 2022, or would you abstain?" If the site is missing, ask for it first; then tell me whether you would answer or abstain.

**Required clarifications:** `["site_id"]`

**Clarification answers:** `{"site_id": "It's in BTS_A."}`

**Goal revisions:** `["Now keep the same site and the same signal, but for the week beginning March 15, 2022, would you answer or abstain based on data quality?", "Compared with the week beginning March 15, 2022, was the week beginning March 22, 2022 better, worse, or about the same for reporting quality?", "Given the second week beginning March 15, 2022 quality result we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-08: Relative 24H Mean Lookup

**Canonical initial request:** Operator handoff: "What was the average min discharge air temperature setpoint threshold on FCU 002 in conference room 010 in BTS_B?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "Use the 24 hours leading up to 00:00 UTC on June 29, 2023."}`

**Goal revisions:** `["Now keep the same signal and site, but shift that 24-hour window back by one day.", "Now keep the same site and the same signal, and if I only know it was around 00:00 UTC on June 27, 2023, give me the nearest available reading.", "For June 27, 2023, would you answer or abstain based on data quality?", "Considering both the timestamped reading and the data-quality check we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-10: Timestamp Nearest Lookup

**Canonical initial request:** Ops ticket: "For BTS_A, what was the air flow measurement on Damper 005 at 00:04 UTC on January 1, 2021?" Use the building telemetry tools and report the logged reading you can justify.

**Required clarifications:** `[]`

**Clarification answers:** `{}`

**Goal revisions:** `["For the week beginning December 28, 2020, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-12: Timestamp Value Lookup

**Canonical initial request:** Operator handoff: "For BTS_A, what value was recorded for the air flow measurement on Damper 005?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean 16:11:13.972 UTC on July 12, 2022."}`

**Goal revisions:** `["Now keep the same signal and site, but if I only know it was around 16:11 UTC on July 12, 2022, give me the nearest available reading.", "For the week beginning July 11, 2022, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-14: Window Mean Lookup

**Canonical initial request:** Operator handoff: "In BTS_B, what was the weekly average for the air temperature measurement on FCU 005 in conference room 011?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean the week beginning January 5, 2021."}`

**Goal revisions:** `["Now keep the same signal and site, but give me the previous week.", "Now keep the same site and the same signal, and if I only know it was around 00:04 UTC on December 29, 2020, give me the nearest available reading.", "For the week beginning December 29, 2020, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-16: Window Pairwise Compare

**Canonical initial request:** Operator handoff: "In BTS_A, which side averaged higher for usage measurement: gas meter 001 or gas meter 002 in building 001?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean the week beginning February 2, 2021."}`

**Goal revisions:** `["Now keep the same two signals and site, but compare the previous week.", "Now keep the same site and the winning signal from the last answer, and if I only know it was around 23:56 UTC on February 1, 2021, give me the nearest available reading.", "Based only on the two data-quality checks for the winning signal from the first and second results we just discussed, which one would you trust more as a reporting basis: the first or the second?", "Based on the last data-quality result we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which streams or points did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-18: Window Rank

**Canonical initial request:** Operator handoff: "Across room locations in BTS_A, which air flow reading stream had the highest average?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean August 2022."}`

**Goal revisions:** `["Now keep the same candidate group and site, but rank the previous month.", "Compared with the month beginning July 1, 2022, did the top-ranked stream for the month beginning August 1, 2022 stay the same or change?", "Now keep the same site and the winning signal from the second month, and for the month beginning July 1, 2022, would you answer or abstain based on data quality?", "Given the second month's winner and its month-bounded quality result for the month beginning July 1, 2022 we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which streams or points did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.
