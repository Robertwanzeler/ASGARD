# Scenario 2 - KPM RC LAB (RC disabled)

## Objective
Validate the lab version of xapp_kpm_rc without sending RC control actions.

## Components
- nearRT-RIC
- emu_agent_gnb
- xapp_kpm_rc_lab

## Expected behavior
- E2 setup succeeds
- E42 setup succeeds
- KPM subscription succeeds
- KPM indication messages are received
- RC disabled message appears in log
- xApp stops cleanly

## Purpose
This scenario validates the lab xApp structure before enabling RC control logic.
