#!/bin/bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/
