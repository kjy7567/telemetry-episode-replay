# Domain-Practitioner Validation Protocol

## Purpose

This study tests whether the benchmark's interaction obligations resemble requests and follow-ups used in building operations. It is separate from executable correctness testing.

## Ethics and Participants

Complete any ethics review, exemption determination, or other institutional requirement that applies before collecting responses. Recruit at least two practitioners with two or more years of experience in building operations, BMS/BAS engineering, HVAC controls, commissioning, or closely related telemetry analysis. Record role category and years of experience, but do not collect names, employers, email addresses, or site-identifying information.

## Materials

Run:

```bash
python scripts/build_human_validation_packet.py
```

The command creates 18 cards, two for each benchmark family:

- `packet/01_blind_authoring_cards.csv`
- `packet/02_canonical_review_cards.csv`
- `packet/03_responses.csv`

It also creates separate readable forms for blind authoring and later canonical review:

- `packet/01_group_A_blind_form.md` and `packet/01_group_B_blind_form.md`
- `packet/02_group_A_canonical_review.md` and `packet/02_group_B_canonical_review.md`

Assign group A cards to one practitioner and group B cards to another. Each practitioner receives one card from every family.

Give each participant `PARTICIPANT_INFORMATION.md` before any study material. The participant must record `consent_to_participate=yes`; quotation consent is separate.

## Procedure

1. Provide `PARTICIPANT_INFORMATION.md`, answer procedural questions, and obtain affirmative participation consent.
2. Give the practitioner only `01_group_A_blind_form.md` or `01_group_B_blind_form.md` for the assigned group.
3. For each structured telemetry intent, ask the practitioner to write the initial request and any clarification reply, goal revision, quality-decision request, and evidence request they would realistically use. They must write `NONE` when a turn would not occur.
4. Do not show generated benchmark language during blind authoring. Do not allow generative writing or paraphrasing tools.
5. After the practitioner finishes a card, reveal the matching section from that group's canonical review form.
6. Ask the practitioner to complete all ratings in `03_responses.csv`. Preserve authored text verbatim except for removal of identifying details.
7. Obtain explicit permission before quoting a response. Record only `yes` or `no` in `consent_to_quote`.

Allowed categorical values are documented here and enforced by the analysis script:

- obligation match: `match`, `partial`, `mismatch`
- realistic fields: `yes`, `no`, or `not_applicable` where offered
- meaning preserved: `yes`, `partial`, `no`
- workflow use: `yes`, `with_edits`, `no`
- consent to participate: `yes`
- consent to quote: `yes`, `no`

## Analysis

After all 18 responses are complete, run:

```bash
python scripts/analyze_human_validation.py
```

The analyzer refuses to produce a result when consent is absent, fields are missing, a participant has less than two years of experience, participant metadata are inconsistent, fewer than two practitioners are represented, fewer than 18 responses are present, or a family has fewer than two responses. It writes `results.json` and a paste-ready `results.md` with participant eligibility, overall and family-level naturalness, obligation match, meaning preservation, workflow usability, issue codes, Wilson 95% intervals for proportions, and only those verbatim examples authorized for quotation.

## Reporting

Report participant eligibility, the number of practitioners and responses, the blind-before-review procedure, all denominators, and both positive and negative examples. This validation measures interaction realism and semantic fit; executable telemetry correctness remains covered by the deterministic contracts and tools.
