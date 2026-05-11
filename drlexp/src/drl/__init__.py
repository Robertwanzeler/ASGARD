"""
GreenRAN DRL Experiment Package
================================
Deep Reinforcement Learning implementation for GreenRAN Open RAN resource allocation.
Based on IEEE paper: "Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing"

Modules:
    - models: SBiLSTM, Actor, Critic neural networks
    - gym_environment: Gymnasium wrapper for GreenRAN
    - replay_buffer: Experience replay memory
    - metrics: Energy efficiency, isolation metrics
"""

__version__ = "1.0.0"
__author__ = "GreenRAN Team - UFPA"

from .replay_buffer import ReplayBuffer
from .gym_environment import GreenRANGymEnv

__all__ = [
    'ReplayBuffer',
    'GreenRANGymEnv',
]