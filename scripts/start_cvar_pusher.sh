#!/bin/bash
cd /home/robert/orange_nuclear
nohup python3 push_cvar_to_influx.py >> /tmp/cvar.log 2>&1 &
echo $! > /tmp/cvar.pid
echo "Started CVaR pusher with PID $(cat /tmp/cvar.pid)"
