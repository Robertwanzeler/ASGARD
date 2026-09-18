#!/bin/bash
# GreenRAN - Iniciar ns-3
cd ~/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran

SIM_TIME="${SIM_TIME:-600}"
./build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime="${SIM_TIME}"
