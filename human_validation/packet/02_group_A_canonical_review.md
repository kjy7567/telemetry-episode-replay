# Canonical Review Form: Group A

Open this form only after completing the blind authoring form.

## HV-01: Day Mean Lookup

**Canonical initial request:** Operator handoff: "For BTS_C, what was the average electrical power reading on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean May 22, 2020."}`

**Goal revisions:** `["Now keep the same signal and site, but give me the next day.", "Now keep the same site and the same signal, and if I only know it was around 23:56 UTC on May 22, 2020, give me the nearest available reading.", "For May 23, 2020, would you answer or abstain based on data quality?", "Considering both the timestamped reading and the data-quality check we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-03: Point Disambiguation

**Canonical initial request:** Operator handoff: "Which stream should I use for the electrical energy sensor on Electrical Meter 042?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["site_id"]`

**Clarification answers:** `{"site_id": "It's in BTS_C."}`

**Goal revisions:** `["Now keep the same site and measurement type, but if the operator meant Electrical Meter 061 instead, which stream should I use?", "Now keep the same site and that signal, and if I only know it was around 01:15 UTC on November 18, 2020, give me the nearest available reading.", "For the week beginning November 16, 2020, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-05: Quality Gate

**Canonical initial request:** Data-quality review request: "Would you trust this signal enough for the weekly trend question about the air differential pressure measurement on Zone 005 for the week beginning May 19, 2020, or would you abstain?" If the site is missing, ask for it first; then tell me whether you would answer or abstain.

**Required clarifications:** `["site_id"]`

**Clarification answers:** `{"site_id": "It's in BTS_C."}`

**Goal revisions:** `["Now keep the same site and the same signal, but for the week beginning May 12, 2020, would you answer or abstain based on data quality?", "Compared with the week beginning May 12, 2020, was the week beginning May 19, 2020 better, worse, or about the same for reporting quality?", "Given the second week beginning May 12, 2020 quality result we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-07: Relative 24H Mean Lookup

**Canonical initial request:** Operator handoff: "What was the average over the previous 24 hours for the electrical power reading on Supply Fan 035 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "Use the 24 hours leading up to 00:00 UTC on January 18, 2024."}`

**Goal revisions:** `["Now keep the same signal and site, but shift that 24-hour window back by one day.", "Now keep the same site and the same signal, and if I only know it was around 00:01 UTC on January 16, 2024, give me the nearest available reading.", "For January 16, 2024, would you answer or abstain based on data quality?", "Considering both the timestamped reading and the data-quality check we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-09: Timestamp Nearest Lookup

**Canonical initial request:** Ops ticket: "For BTS_C, what was the air differential pressure reading on Zone 005 at 00:21 UTC on May 22, 2020?" Use the building telemetry tools and report the logged reading you can justify.

**Required clarifications:** `[]`

**Clarification answers:** `{}`

**Goal revisions:** `["For the week beginning May 18, 2020, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-11: Timestamp Value Lookup

**Canonical initial request:** Operator handoff: "What was the air differential pressure reading on Zone 005 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean 07:03:23.64 UTC on February 3, 2022."}`

**Goal revisions:** `["Now keep the same signal and site, but if I only know it was around 07:03 UTC on February 3, 2022, give me the nearest available reading.", "For the week beginning January 31, 2022, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-13: Window Mean Lookup

**Canonical initial request:** Operator handoff: "For BTS_C, what was the average electrical power measurement on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean the week beginning May 26, 2020."}`

**Goal revisions:** `["Now keep the same signal and site, but give me the previous week.", "Now keep the same site and the same signal, and if I only know it was around 00:04 UTC on May 19, 2020, give me the nearest available reading.", "For the week beginning May 19, 2020, would you answer or abstain based on data quality?", "Given the public-time reading and the data-quality check, should I report it as-is, abstain, or ask for more time detail before reporting it?"]`

**Evidence follow-up:** Which stream or point did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-15: Window Pairwise Compare

**Canonical initial request:** Operator handoff: "In BTS_C, which side averaged higher for run time reading: Terminal Unit 001 or Floor 008?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean the week beginning May 2, 2023."}`

**Goal revisions:** `["Now keep the same two signals and site, but compare the previous week.", "Now keep the same site and the winning signal from the last answer, and if I only know it was around 23:56 UTC on May 1, 2023, give me the nearest available reading.", "Based only on the two data-quality checks for the winning signal from the first and second results we just discussed, which one would you trust more as a reporting basis: the first or the second?", "Based on the last data-quality result we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which streams or points did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.

## HV-17: Window Rank

**Canonical initial request:** Operator handoff: "In BTS_C, looking across locations, which stream topped the average position sensor readings?" Use the building tools and ask me for any missing site or time detail before querying.

**Required clarifications:** `["time_reference"]`

**Clarification answers:** `{"time_reference": "I mean March 2022."}`

**Goal revisions:** `["Now keep the same candidate group and site, but rank the previous month.", "Compared with the month beginning February 1, 2022, did the top-ranked stream for the month beginning March 1, 2022 stay the same or change?", "Now keep the same site and the winning signal from the second month, and for the month beginning February 1, 2022, would you answer or abstain based on data quality?", "Given the second month's winner and its month-bounded quality result for the month beginning February 1, 2022 we just discussed, should I report it as-is or abstain?"]`

**Evidence follow-up:** Which streams or points did you base that on?

Enter ratings and comments for this card in `03_responses.csv`.
