#!/usr/bin/env bash
DIR="runs/article00/comparison_figures"
echo "=== Reconstruction ==="
eog "$DIR/Reconstruction/"*.png &
echo "=== Indirect ==="
eog "$DIR/Indirect/"*.png &
echo "=== Implicit ==="
eog "$DIR/Implicit/"*.png &
wait
