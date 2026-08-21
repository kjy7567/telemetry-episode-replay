# Telemetry Agent Dataset Bundle

This archive contains the exact datasets and retained evaluation artifacts used
in the paper.

- `bts_agentbench_532/`: 356/87/89 train/dev/test episodes across nine families.
- `xai4heat_agentbench_204/`: 132/31/41 episodes across five temporal families.
- `runs/`: retained GPT-5.5, Gemini 3.1 Pro, and Claude Opus 4.7 BTS traces,
  plus the retained 41-row GPT-5.5 XAI4HEAT trace.
- `runners/`: four command templates, run configurations, and shared runner
  source.

Construction, exact replay, controller, and scoring source is distributed in
`source.zip`. Provider calls are not required to inspect or rescore retained
traces.
