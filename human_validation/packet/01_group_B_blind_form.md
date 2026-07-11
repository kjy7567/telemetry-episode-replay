# Blind Authoring Form: Group B

Complete this form before opening the canonical review form. Write NONE for a turn that would not occur.

## HV-02: Day Mean Lookup

- Site: `BTS_B`
- Point context: `[{"point_class": "Min_Discharge_Air_Temperature_Setpoint_Limit", "equipment_label": "BTS_B Fan Coil Unit 002", "location_label": "BTS_B Conference Room 010"}]`
- Requested operation: `{"operation": "aggregate_window", "metric": "mean_value", "window_start": "2021-07-01 00:00:00+00:00", "window_end": "2021-07-02 00:00:00+00:00", "period": "day"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-04: Point Disambiguation

- Site: `BTS_A`
- Point context: `[{"point_class": "Mode_Status", "equipment_label": "BTS_A Fcu 082"}]`
- Requested operation: `{}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-06: Quality Gate

- Site: `BTS_A`
- Point context: `[{"point_class": "Air_Flow_Sensor", "equipment_label": "BTS_A Exhaust Fan 048"}]`
- Requested operation: `{"operation": "inspect_quality_window", "window_start": "2022-03-22 00:00:00+00:00", "window_end": "2022-03-29 00:00:00+00:00", "period": "week"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-08: Relative 24H Mean Lookup

- Site: `BTS_B`
- Point context: `[{"point_class": "Min_Discharge_Air_Temperature_Setpoint_Limit", "equipment_label": "BTS_B Fan Coil Unit 002", "location_label": "BTS_B Conference Room 010"}]`
- Requested operation: `{"operation": "aggregate_window", "metric": "mean_value", "window_start": "2023-06-28 00:00:00+00:00", "window_end": "2023-06-29 00:00:00+00:00", "period": "custom"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-10: Timestamp Nearest Lookup

- Site: `BTS_A`
- Point context: `[{"point_class": "Air_Flow_Sensor", "equipment_label": "BTS_A Damper 005"}]`
- Requested operation: `{"operation": "lookup_observation", "timestamp": "2021-01-01T00:04:32.484600+00:00", "mode": "nearest"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-12: Timestamp Value Lookup

- Site: `BTS_A`
- Point context: `[{"point_class": "Air_Flow_Sensor", "equipment_label": "BTS_A Damper 005"}]`
- Requested operation: `{"operation": "lookup_observation", "timestamp": "2022-07-12T16:11:13.972000+00:00", "mode": "exact"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-14: Window Mean Lookup

- Site: `BTS_B`
- Point context: `[{"point_class": "Air_Temperature_Sensor", "equipment_label": "BTS_B Fan Coil Unit 005", "location_label": "BTS_B Conference Room 011"}]`
- Requested operation: `{"operation": "aggregate_window", "metric": "mean_value", "window_start": "2021-01-05 00:00:00+00:00", "window_end": "2021-01-12 00:00:00+00:00", "period": "week"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-16: Window Pairwise Compare

- Site: `BTS_A`
- Point context: `[{"point_class": "Usage_Sensor", "equipment_label": "BTS_A Gas Meter 001"}, {"point_class": "Usage_Sensor", "equipment_label": "BTS_A Gas Meter 002", "location_label": "BTS_A Building 001"}]`
- Requested operation: `{"operation": "compare_window", "metric": "mean_value", "window_start": "2021-02-02 00:00:00+00:00", "window_end": "2021-02-09 00:00:00+00:00", "period": "week"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-18: Window Rank

- Site: `BTS_A`
- Point context: `[{"point_class": "Air_Flow_Sensor", "location_type": "Room"}]`
- Requested operation: `{"operation": "rank_window", "metric": "mean_value", "window_start": "2022-08-01 00:00:00+00:00", "window_end": "2022-09-01 00:00:00+00:00", "period": "month", "order": "desc", "topk": 1}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:
