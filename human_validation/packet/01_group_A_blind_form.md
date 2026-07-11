# Blind Authoring Form: Group A

Complete this form before opening the canonical review form. Write NONE for a turn that would not occur.

## HV-01: Day Mean Lookup

- Site: `BTS_C`
- Point context: `[{"point_class": "Electrical_Power_Sensor", "equipment_label": "BTS_C Supply Fan 035"}]`
- Requested operation: `{"operation": "aggregate_window", "metric": "mean_value", "window_start": "2020-05-22 00:00:00+00:00", "window_end": "2020-05-23 00:00:00+00:00", "period": "day"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-03: Point Disambiguation

- Site: `BTS_C`
- Point context: `[{"point_class": "Electrical_Energy_Sensor", "equipment_label": "BTS_C Electrical Meter 042"}]`
- Requested operation: `{}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-05: Quality Gate

- Site: `BTS_C`
- Point context: `[{"point_class": "Air_Differential_Pressure_Sensor", "equipment_label": "BTS_C Zone 005"}]`
- Requested operation: `{"operation": "inspect_quality_window", "window_start": "2020-05-19 00:00:00+00:00", "window_end": "2020-05-26 00:00:00+00:00", "period": "week"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-07: Relative 24H Mean Lookup

- Site: `BTS_C`
- Point context: `[{"point_class": "Electrical_Power_Sensor", "equipment_label": "BTS_C Supply Fan 035"}]`
- Requested operation: `{"operation": "aggregate_window", "metric": "mean_value", "window_start": "2024-01-17 00:00:00+00:00", "window_end": "2024-01-18 00:00:00+00:00", "period": "custom"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-09: Timestamp Nearest Lookup

- Site: `BTS_C`
- Point context: `[{"point_class": "Air_Differential_Pressure_Sensor", "equipment_label": "BTS_C Zone 005"}]`
- Requested operation: `{"operation": "lookup_observation", "timestamp": "2020-05-22T00:21:41.526000+00:00", "mode": "nearest"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-11: Timestamp Value Lookup

- Site: `BTS_C`
- Point context: `[{"point_class": "Air_Differential_Pressure_Sensor", "equipment_label": "BTS_C Zone 005"}]`
- Requested operation: `{"operation": "lookup_observation", "timestamp": "2022-02-03T07:03:23.640000+00:00", "mode": "exact"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-13: Window Mean Lookup

- Site: `BTS_C`
- Point context: `[{"point_class": "Electrical_Power_Sensor", "equipment_label": "BTS_C Supply Fan 035"}]`
- Requested operation: `{"operation": "aggregate_window", "metric": "mean_value", "window_start": "2020-05-26 00:00:00+00:00", "window_end": "2020-06-02 00:00:00+00:00", "period": "week"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-15: Window Pairwise Compare

- Site: `BTS_C`
- Point context: `[{"point_class": "Run_Time_Sensor", "equipment_label": "BTS_C Terminal Unit 001"}, {"point_class": "Run_Time_Sensor", "equipment_label": "BTS_C Floor 008"}]`
- Requested operation: `{"operation": "compare_window", "metric": "mean_value", "window_start": "2023-05-02 00:00:00+00:00", "window_end": "2023-05-09 00:00:00+00:00", "period": "week"}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

## HV-17: Window Rank

- Site: `BTS_C`
- Point context: `[{"point_class": "Position_Sensor", "location_type": "Location"}]`
- Requested operation: `{"operation": "rank_window", "metric": "mean_value", "window_start": "2022-03-01 00:00:00+00:00", "window_end": "2022-04-01 00:00:00+00:00", "period": "month", "order": "desc", "topk": 1}`

Initial request:

Clarification reply, if any:

Realistic goal revision, if any:

Quality-decision request, if any:

Evidence request, if any:

