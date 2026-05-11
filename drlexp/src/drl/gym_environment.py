#!/usr/bin/env python3
"""
GreenRAN Gymnasium Environment
================================
Wrapper for integrating GreenRAN simulation with Gymnasium (formerly OpenAI Gym).
Based on the IEEE paper's approach for RL in network slicing.

This environment provides:
- State space: Network metrics (CVaR, latency, throughput, etc.)
- Action space: Hybrid (ALLOWED/CONDITIONAL/BLOCKED × Potência)
- Reward: Energy efficiency - SLA penalty balance

Author: GreenRAN Team - UFPA
"""

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import sqlite3
from typing import Tuple, Dict, Any, Optional


class GreenRANGymEnv(gym.Env):
    """
    Gymnasium environment for GreenRAN resource allocation.
    
    This environment simulates the GreenRAN network and provides
    an interface for RL agents (like A3C) to learn optimal resource
    allocation policies.
    
    State Space (18 features):
        - Network metrics: cvar_ms, cvar_trend, cvar_acceleration, latency_p95_ms, 
                          jitter_ms, packet_loss_pct, throughput_mbps
        - Resources: active_ues, active_cameras, critical_ues, camera_ratio, 
                     critical_ue_ratio, allocated_rbs
        - Power: current_power, power_budget
        - Time: hour_sin, hour_cos
    
    Action Space (9 hybrid actions):
        - Format: (decision, power_adjustment)
        - decision: 0=ALLOWED, 1=CONDITIONAL, 2=BLOCKED
        - power_adjustment: 0=Reduce, 1=Maintain, 2=Increase
    
    Reward Function:
        - Positive if CVaR < 60ms (green zone)
        - Moderate if 60ms <= CVaR < 80ms (yellow zone)
        - Negative if CVaR >= 80ms (red zone)
        - Bonus for energy saving
        - Penalty for SLA violation
    """
    
    metadata = {'render_modes': ['human']}
    
    def __init__(self, db_path: str = "/tmp/rapp_data_lake.db", 
                 prediction_window: int = 1200,
                 render_mode: Optional[str] = None):
        """
        Initialize the GreenRAN Gym environment.
        
        Args:
            db_path: Path to the SQLite data lake
            prediction_window: Prediction window size (PW from paper)
            render_mode: Rendering mode for visualization
        """
        super().__init__()
        
        self.db_path = db_path
        self.prediction_window = prediction_window
        self.render_mode = render_mode
        
        # Connect to data lake
        self.conn = None
        self.cursor = None
        
        # State tracking
        self.current_step = 0
        self.current_data = None
        self.state_history = []
        
        # Action space: 9 hybrid actions (3 decisions × 3 power levels)
        self.action_space = spaces.Discrete(9)
        
        # Observation space: 18 features
        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(18,),
            dtype=np.float32
        )
        
        # Reward thresholds (from project)
        self.threshold_warning = 60.0   # ms - warning zone
        self.threshold_critical = 80.0  # ms - critical zone
        self.threshold_sla = 100.0      # ms - SLA violation
        
        # Energy weights
        self.w1_energy = 1.0   # Weight for energy saving
        self.w2_sla = 2.0      # Weight for SLA penalty
        
    def _connect_db(self):
        """Connect to the data lake."""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            
    def _load_current_state(self, step: Optional[int] = None) -> Optional[Dict]:
        """
        Load current state from data lake.
        
        Args:
            step: Specific step to load, or None for current
            
        Returns:
            Dictionary with state metrics
        """
        self._connect_db()
        
        if step is None:
            # Get latest metrics
            query = """
                SELECT 
                    m.cvar_per_ue_us,
                    m.latency_p95_per_ue_us,
                    m.global_avg_latency_us,
                    m.global_jitter_us,
                    m.global_packet_loss_rate,
                    m.throughput_kbps,
                    m.total_active_ues,
                    m.total_active_cameras,
                    m.total_critical_ues,
                    m.total_tx_bytes,
                    m.total_rx_bytes,
                    m.variance_per_ue_us2,
                    m.sim_time_s
                FROM extended_metrics m
                ORDER BY m.timestamp DESC
                LIMIT 1
            """
        else:
            # Get specific step
            query = f"""
                SELECT 
                    m.cvar_per_ue_us,
                    m.latency_p95_per_ue_us,
                    m.global_avg_latency_us,
                    m.global_jitter_us,
                    m.global_packet_loss_rate,
                    m.throughput_kbps,
                    m.total_active_ues,
                    m.total_active_cameras,
                    m.total_critical_ues,
                    m.total_tx_bytes,
                    m.total_rx_bytes,
                    m.variance_per_ue_us2,
                    m.sim_time_s
                FROM extended_metrics m
                ORDER BY m.timestamp
                LIMIT 1 OFFSET {step}
            """
        
        try:
            result = self.cursor.fetchone()
            if result is None:
                return None
                
            cvar_ms = result[0] / 1000.0 if result[0] else 0
            latency_p95_ms = result[1] / 1000.0 if result[1] else 0
            avg_latency_ms = result[2] / 1000.0 if result[2] else 0
            jitter_ms = result[3] / 1000.0 if result[3] else 0
            packet_loss_pct = (result[4] * 100) if result[4] else 0
            throughput_mbps = result[5] / 1000.0 if result[5] else 0
            active_ues = result[6] or 0
            active_cameras = result[7] or 0
            critical_ues = result[8] or 0
            tx_bytes = result[9] or 0
            rx_bytes = result[10] or 0
            variance_ms2 = result[11] / 1_000_000.0 if result[11] else 0
            
            # Derived metrics
            camera_ratio = active_cameras / max(active_ues, 1)
            critical_ue_ratio = critical_ues / max(active_ues, 1)
            tx_rx_ratio = tx_bytes / max(rx_bytes, 1)
            
            # Compute trend features from history
            cvar_trend = 0
            cvar_acceleration = 0
            
            if len(self.state_history) >= 5:
                cvar_trend = cvar_ms - self.state_history[-5]['cvar_ms']
                
            if len(self.state_history) >= 2:
                prev_diff = self.state_history[-1]['cvar_ms'] - self.state_history[-2]['cvar_ms']
                curr_diff = cvar_ms - self.state_history[-1]['cvar_ms']
                cvar_acceleration = curr_diff - prev_diff
            
            # Power (placeholder - would come from simulator)
            current_power = 20.0  # dBm - default
            power_budget = 30.0   # dBm - maximum
            
            # Time features (hour from current time)
            from datetime import datetime
            now = datetime.now()
            hour = now.hour
            hour_sin = np.sin(2 * np.pi * hour / 24)
            hour_cos = np.cos(2 * np.pi * hour / 24)
            
            # Build state dict
            state = {
                'cvar_ms': cvar_ms,
                'cvar_trend': cvar_trend,
                'cvar_acceleration': cvar_acceleration,
                'latency_p95_ms': latency_p95_ms,
                'jitter_ms': jitter_ms,
                'packet_loss_pct': packet_loss_pct,
                'throughput_mbps': throughput_mbps,
                'active_ues': active_ues,
                'active_cameras': active_cameras,
                'critical_ues': critical_ues,
                'camera_ratio': camera_ratio,
                'critical_ue_ratio': critical_ue_ratio,
                'allocated_rbs': active_ues * 10,  # Estimate
                'current_power': current_power,
                'power_budget': power_budget,
                'hour_sin': hour_sin,
                'hour_cos': hour_cos
            }
            
            return state
            
        except Exception as e:
            print(f"[Gym] Error loading state: {e}")
            return None
    
    def _state_to_array(self, state: Dict) -> np.ndarray:
        """
        Convert state dict to numpy array for observation.
        
        Args:
            state: State dictionary
            
        Returns:
            NumPy array with 18 features
        """
        features = [
            state['cvar_ms'],
            state['cvar_trend'],
            state['cvar_acceleration'],
            state['latency_p95_ms'],
            state['jitter_ms'],
            state['packet_loss_pct'],
            state['throughput_mbps'],
            state['active_ues'],
            state['active_cameras'],
            state['critical_ues'],
            state['camera_ratio'],
            state['critical_ue_ratio'],
            state['allocated_rbs'],
            state['current_power'],
            state['power_budget'],
            state['hour_sin'],
            state['hour_cos'],
            state.get('variance_ms2', 0)  # Add variance as 18th
        ]
        
        return np.array(features, dtype=np.float32)
    
    def _compute_reward(self, state: Dict, action: int, next_state: Dict) -> float:
        """
        Compute reward based on state and action.
        
        Reward = w1 * Energy_Saved - w2 * Penalty_SLA
        
        Args:
            state: Current state
            action: Action taken
            next_state: Next state
            
        Returns:
            Reward value
        """
        cvar = next_state['cvar_ms']
        
        # Decode action
        decision = action // 3  # 0=ALLOWED, 1=CONDITIONAL, 2=BLOCKED
        power_adjust = action % 3  # 0=Reduce, 1=Maintain, 2=Increase
        
        # Power adjustment effect (placeholder)
        if power_adjust == 0:
            power_change = -5  # dBm
        elif power_adjust == 1:
            power_change = 0
        else:
            power_change = 5  # dBm
            
        power_saved = -power_change * 0.1  # Assume saving when reducing
        
        # Base reward based on CVaR zones
        if cvar < self.threshold_warning:
            base_reward = 1.0
        elif cvar < self.threshold_critical:
            base_reward = 0.5
        elif cvar < self.threshold_sla:
            base_reward = 0.0
        else:
            # SLA violation - exponential penalty
            base_reward = -1.0 * np.exp((cvar - self.threshold_sla) / 50)
        
        # Energy bonus
        energy_bonus = power_saved * self.w1_energy * 0.1
        
        # Penalty for unnecessary blocking
        action_penalty = 0
        if decision == 2 and cvar < self.threshold_warning:
            action_penalty = -0.2
            
        total_reward = base_reward + energy_bonus + action_penalty
        
        return float(np.clip(total_reward, -10, 10))
    
    def reset(self, seed: Optional[int] = None, options: Optional[Dict] = None) -> Tuple[np.ndarray, Dict]:
        """
        Reset the environment to initial state.
        
        Args:
            seed: Random seed
            options: Additional options
            
        Returns:
            Initial observation and info dict
        """
        super().reset(seed=seed)
        
        # Reset state tracking
        self.current_step = 0
        self.state_history = []
        
        # Load initial state
        state = self._load_current_state(0)
        
        if state is None:
            # If no data, create default state
            state = {
                'cvar_ms': 50.0,
                'cvar_trend': 0.0,
                'cvar_acceleration': 0.0,
                'latency_p95_ms': 60.0,
                'jitter_ms': 2.0,
                'packet_loss_pct': 0.1,
                'throughput_mbps': 100.0,
                'active_ues': 20,
                'active_cameras': 3,
                'critical_ues': 2,
                'camera_ratio': 0.15,
                'critical_ue_ratio': 0.1,
                'allocated_rbs': 200,
                'current_power': 20.0,
                'power_budget': 30.0,
                'hour_sin': 0.0,
                'hour_cos': 1.0,
                'variance_ms2': 10.0
            }
        
        self.state_history.append(state)
        observation = self._state_to_array(state)
        
        info = {
            'step': self.current_step,
            'state': state
        }
        
        return observation, info
    
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict]:
        """
        Execute one step in the environment.
        
        Args:
            action: Action to take (0-8)
            
        Returns:
            observation: New state
            reward: Reward received
            terminated: Whether episode ended
            truncated: Whether episode was truncated
            info: Additional information
        """
        # Get current state (before action)
        current_state = self.state_history[-1] if self.state_history else None
        
        # Increment step
        self.current_step += 1
        
        # Load next state (simulated effect of action)
        next_state = self._load_current_state(self.current_step)
        
        if next_state is None:
            # If no more data, simulate next state
            if current_state is not None:
                # Simulate based on action
                decision = action // 3
                power_adjust = action % 3
                
                # Effect of action on CVaR
                if decision == 0:  # ALLOWED
                    cvar_change = np.random.uniform(-5, 10)
                elif decision == 1:  # CONDITIONAL
                    cvar_change = np.random.uniform(-10, 5)
                else:  # BLOCKED
                    cvar_change = np.random.uniform(-20, -10)
                    
                # Effect of power adjustment
                if power_adjust == 0:  # Reduce
                    cvar_change += 5
                elif power_adjust == 2:  # Increase
                    cvar_change -= 5
                    
                next_state = current_state.copy()
                next_state['cvar_ms'] = max(0, current_state['cvar_ms'] + cvar_change)
                next_state['cvar_trend'] = cvar_change
                
            else:
                next_state = self._get_default_state()
        
        # Compute reward
        reward = self._compute_reward(current_state or {}, action, next_state)
        
        # Check termination
        # Terminate if max steps or severe SLA violation
        terminated = False
        if next_state['cvar_ms'] > 150:  # Severe SLA violation
            terminated = True
            
        truncated = self.current_step >= 1000  # Max steps
        
        # Update history
        self.state_history.append(next_state)
        if len(self.state_history) > 1200:  # Keep only PW history
            self.state_history = self.state_history[-1200:]
            
        observation = self._state_to_array(next_state)
        
        info = {
            'step': self.current_step,
            'action': action,
            'decision': action // 3,
            'power_adjust': action % 3,
            'state': next_state,
            'cvar_ms': next_state['cvar_ms']
        }
        
        return observation, reward, terminated, truncated, info
    
    def _get_default_state(self) -> Dict:
        """Get default state when no data available."""
        from datetime import datetime
        now = datetime.now()
        hour = now.hour
        
        return {
            'cvar_ms': 50.0,
            'cvar_trend': 0.0,
            'cvar_acceleration': 0.0,
            'latency_p95_ms': 60.0,
            'jitter_ms': 2.0,
            'packet_loss_pct': 0.1,
            'throughput_mbps': 100.0,
            'active_ues': 20,
            'active_cameras': 3,
            'critical_ues': 2,
            'camera_ratio': 0.15,
            'critical_ue_ratio': 0.1,
            'allocated_rbs': 200,
            'current_power': 20.0,
            'power_budget': 30.0,
            'hour_sin': np.sin(2 * np.pi * hour / 24),
            'hour_cos': np.cos(2 * np.pi * hour / 24),
            'variance_ms2': 10.0
        }
    
    def render(self):
        """Render the environment (optional)."""
        if self.render_mode == 'human':
            state = self.state_history[-1] if self.state_history else {}
            print(f"Step: {self.current_step}")
            print(f"CVaR: {state.get('cvar_ms', 0):.2f} ms")
            print(f"Active UEs: {state.get('active_ues', 0)}")
            print(f"Cameras: {state.get('active_cameras', 0)}")
    
    def close(self):
        """Close the environment and clean up resources."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def get_action_meanings(self) -> Dict[int, str]:
        """
        Get human-readable action meanings.
        
        Returns:
            Dictionary mapping action IDs to descriptions
        """
        decisions = ['ALLOWED', 'CONDITIONAL', 'BLOCKED']
        powers = ['REDUCE', 'MAINTAIN', 'INCREASE']
        
        meanings = {}
        for i in range(9):
            decision = decisions[i // 3]
            power = powers[i % 3]
            meanings[i] = f"{decision} + {power}"
            
        return meanings


# Registration function for Gymnasium
def register_environment():
    """Register this environment with Gymnasium."""
    from gymnasium import register
    
    register(
        id='greenran-v0',
        entry_point='drl.gym_environment:GreenRANGymEnv',
        max_episode_steps=1000,
        reward_threshold=100.0
    )


# Standalone test
if __name__ == "__main__":
    # Create environment
    env = GreenRANGymEnv()
    
    print("=" * 60)
    print("GreenRAN Gymnasium Environment Test")
    print("=" * 60)
    
    # Test action meanings
    print("\nAction Space:")
    meanings = env.get_action_meanings()
    for action_id, meaning in meanings.items():
        print(f"  {action_id}: {meaning}")
    
    print("\nObservation Space:")
    print(f"  Shape: {env.observation_space.shape}")
    print(f"  Low: {env.observation_space.low}")
    print(f"  High: {env.observation_space.high}")
    
    # Reset environment
    print("\nResetting environment...")
    observation, info = env.reset()
    print(f"Initial observation shape: {observation.shape}")
    print(f"Initial state: {info['state']}")
    
    # Take a few steps
    print("\nTaking 5 random steps...")
    for i in range(5):
        action = env.action_space.sample()
        observation, reward, terminated, truncated, info = env.step(action)
        print(f"  Step {i+1}: Action={action}, Reward={reward:.3f}, CVaR={info['cvar_ms']:.2f}ms")
        
        if terminated or truncated:
            break
    
    # Close environment
    env.close()
    
    print("\n✅ GreenRAN Gym environment test passed!")