"""
GreenRAN DRL Experiment Package
===============================

The original package targets the legacy EE-DRL energy-control line. The SAC
migration adds a parallel CAORA-style environment without rewriting the legacy
modules in place.
"""

__version__ = "1.0.0"
__author__ = "GreenRAN Team - UFPA"

try:
    from .caora_sac_environment import CAORASACEnv
except Exception:
    CAORASACEnv = None

from .replay_buffer import ReplayBuffer

try:
    from .gym_environment import GreenRANGymEnv
except Exception:
    GreenRANGymEnv = None

__all__ = [
    'CAORASACEnv',
    'ReplayBuffer',
    'GreenRANGymEnv',
]
