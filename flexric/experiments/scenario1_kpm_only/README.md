# Scenario 1 - KPM Only Baseline

## Objective
Validate the basic FlexRIC monitoring pipeline without RC control actions.

## Components
- nearRT-RIC
- emu_agent_gnb
- xapp_kpm_moni

## Expected behavior
- E2 setup succeeds
- E42 setup succeeds
- KPM subscription succeeds
- KPM indication messages are received
- xApp stops cleanly

## Purpose
This is the baseline scenario for later comparison with:
- RC-enabled scenario
- load test scenario
- ns-3/BonnMotion scenario

## Logs
- logs/ric.log
- logs/agent.log
- logs/xapp.log

## Results
- results/summary.txt
- results/git_commit.txt
- results/git_branch.txt
