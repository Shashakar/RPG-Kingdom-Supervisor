# Autonomous operating windows and approved work plans

Issue #109 adds a host-owned, model-free policy layer for bounded unattended work.

The operator explicitly configures an enabled operating window, quota floors, and an ordered approved work plan. The policy never invents backlog work and never auto-merges. Human-review, human-attention, manual-action, and unknown or non-recoverable halt states stop the dependency lane.

The deterministic policy core is scripts/autonomous-plan.py. It handles midnight-crossing windows, dependencies, active-worker exclusion, quota waiting, recoverable halt classification, and human gates. config/autonomous-plan.example.json documents the configuration shape.

This initial slice deliberately separates evaluation from mutation. A host service must feed authoritative lifecycle and quota state into the policy and may perform existing rearm and ready operations only when the result is eligible. This keeps scheduling decisions testable without a model call or Unity.
