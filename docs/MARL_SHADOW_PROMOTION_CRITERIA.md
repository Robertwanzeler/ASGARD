# MARL Shadow Promotion Criteria

## Objective

Define when the article-aligned TA-SAM MARL path can move across stages:

- `bootstrap_partial`
- `shadow_ready`
- `control_candidate`

## Current Gates

### bootstrap_partial

- actor updates exist
- post-warmup actor updates exist
- but one or more stability conditions still fail

### shadow_ready

- actor updates persist after warmup
- selective threshold is active after warmup
- final `critic_loss <= 5e-4`
- no blocking reasons remain

### control_candidate

- `shadow_ready` already true
- final `critic_loss <= 3e-4`
- final `selected_fraction` in `[0.05, 0.25]`
- at least 5 epochs completed
- dynamic threshold active after warmup

## Operational Rule

Even when `control_candidate=true`, runtime must still stay in shadow mode until:

1. the same checkpoint remains stable across repeated collections;
2. ARMD-GreenRAN remains unchanged;
3. the MARL proposal is compared against the live allocator over multiple windows;
4. a separate approval step promotes it from observation to actuation.
