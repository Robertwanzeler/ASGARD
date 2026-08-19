#!/usr/bin/env python3
import random
import sys

def generate_static(filename, num_nodes, x_max=1000, y_max=1000):
    with open(filename, 'w') as f:
        for i in range(num_nodes):
            x = random.uniform(0, x_max)
            y = random.uniform(0, y_max)
            f.write(f"$node_({i}) set X_ {x:.2f}\n")
            f.write(f"$node_({i}) set Y_ {y:.2f}\n")
            f.write(f"$ns_ at 0.0 \"$node_({i}) setdest {x:.2f} {y:.2f} 0.0\"\n")

def generate_walk(filename, num_nodes, speed_min, speed_max, x_max=1000, y_max=1000, duration=300, updates=10):
    with open(filename, 'w') as f:
        for i in range(num_nodes):
            x = random.uniform(0, x_max)
            y = random.uniform(0, y_max)
            f.write(f"$node_({i}) set X_ {x:.2f}\n")
            f.write(f"$node_({i}) set Y_ {y:.2f}\n")
            
            curr_x, curr_y = x, y
            time_step = duration / updates
            for t in range(0, int(duration), int(time_step)):
                next_x = random.uniform(0, x_max)
                next_y = random.uniform(0, y_max)
                speed = random.uniform(speed_min, speed_max)
                f.write(f"$ns_ at {t:.1f} \"$node_({i}) setdest {next_x:.2f} {next_y:.2f} {speed:.2f}\"\n")

generate_static('ns-O-RAN-flexric/mmwave-LENA-oran/mobility/scenario100_static.ns_movements', 50)
generate_walk('ns-O-RAN-flexric/mmwave-LENA-oran/mobility/scenario100_pedestrian.ns_movements', 40, 1.0, 2.0)
generate_walk('ns-O-RAN-flexric/mmwave-LENA-oran/mobility/scenario100_vehicle.ns_movements', 10, 10.0, 20.0)
print("Arquivos de mobilidade gerados com sucesso!")
