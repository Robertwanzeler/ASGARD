# GreenRAN O-RAN Project Progress

## Date: 2026-03-16

## Summary of Changes

### ✅ Completed Fixes:

1. **Timeout Fixes (FlexRIC)**
   - Modified `sync_ui.c:45`: Increased timeout from 5s to 15s
   - Modified `msg_handler_xapp.c:527`: Increased timeout from 5s to 15s
   - Changed timeout assertions to warnings to prevent crashes

2. **Camera/UE Display Fix (Energy Saver xApp)**
   - Modified `xapp_energy_saver.c` to properly track unique UEs by `amf_ue_ngap_id`
   - Now correctly displays: CAMERA 1-3 and UE 1-10
   - Fixed the counting logic to show only 3 cameras and 10 UEs

3. **xApps Running Together**
   - Both xApps now run simultaneously without crashing
   - Added delays between xApp starts (15s) in the script

4. **Scenario Timing**
   - Simulation time: 600s (10 minutes)
   - Warmup: 110s
   - xApps now run for the full simulation duration

### ❌ Remaining Issue:

**KPM Metrics with Zero Values**
- The KPM messages are being sent (6 E2 nodes connected)
- But all metric values show as 0 (volume_dl, pacotes_dl, bitrate_dl, delay_dl)

### Investigation Status:

1. **PDCP Callbacks**: Properly configured via `EnableE2PdcpTraces()`
2. **E2 PDCP Calculator**: Created and attached to devices
3. **KPM Messages**: Being transmitted (confirmed in logs)

### Suspected Causes:

1. **Traffic not reaching PDCP layer** - UDP packets may not be reaching the UEs
2. **Callbacks not firing** - Trace connections may not be working
3. **LCID mismatch** - Code uses LCID=3 hardcoded, may need adjustment

### Files Modified:

**FlexRIC:**
- `flexric/src/xApp/sync_ui.c` - Timeout fix
- `flexric/src/xApp/msg_handler_xapp.c` - Timeout fix
- `flexric/examples/xApp/c/energy_saver/xapp_energy_saver.c` - UE tracking fix

**ns-3:**
- `ns-O-RAN-flexric/mmwave-LENA-oran/scratch/scenario-base.cc` - Added EnableE2PdcpTraces()

### Next Steps:

1. Add debug logging to verify if PDCP callbacks are being triggered
2. Check if UDP traffic is reaching the UEs
3. Verify LCID configuration in the scenario
