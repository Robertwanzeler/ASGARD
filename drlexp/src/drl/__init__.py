"""TA-SAM MARL package for the GreenRAN DRL tracks."""

__version__ = "1.0.0"
__author__ = "GreenRAN Team - UFPA"

from .replay_buffer import ReplayBuffer

try:
    from .ta_sam_marl import TASAMMultiAgentTrainer
except Exception:
    TASAMMultiAgentTrainer = None

try:
    from .ta_sam_marl_sac import TASAMArticleSACTrainer
except Exception:
    TASAMArticleSACTrainer = None

try:
    from .online_greenran_marl_env import OnlineGreenRANMARLEnv
except Exception:
    OnlineGreenRANMARLEnv = None

__all__ = [
    'ReplayBuffer',
    'TASAMMultiAgentTrainer',
    'TASAMArticleSACTrainer',
    'OnlineGreenRANMARLEnv',
]
