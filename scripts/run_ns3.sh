#!/bin/bash
# GreenRAN - Iniciar ns-3
cd ~/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran

./build/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60
