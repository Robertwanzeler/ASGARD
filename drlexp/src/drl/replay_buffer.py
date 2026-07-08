#!/usr/bin/env python3
"""Replay buffers used by the TA-SAM DRL experiments."""

import numpy as np
import random
from collections import deque
from typing import List, Tuple, Any


class ReplayBuffer:
    """
    Experience Replay Buffer for storing and sampling transitions.
    
    The replay buffer stores transitions (s, a, r, s', done) and allows
    random sampling for training neural networks in DRL algorithms.
    
    Attributes:
        capacity: Maximum number of transitions to store
        buffer: deque container for storing transitions
    """
    
    def __init__(self, capacity: int = 100000):
        """
        Initialize the replay buffer.
        
        Args:
            capacity: Maximum number of transitions to store
        """
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)
        self.priorities = deque(maxlen=capacity)  # For prioritized replay
        
    def push(self, state: np.ndarray, action: int, reward: float, 
             next_state: np.ndarray, done: bool, priority: float = 1.0):
        """
        Add a transition to the buffer.
        
        Args:
            state: Current state
            action: Action taken
            reward: Reward received
            next_state: Next state
            done: Whether episode is done
            priority: Priority for prioritized replay (default: 1.0)
        """
        transition = (state, action, reward, next_state, done)
        self.buffer.append(transition)
        self.priorities.append(priority)
        
    def sample(self, batch_size: int) -> Tuple:
        """
        Randomly sample a batch of transitions.
        
        Args:
            batch_size: Number of transitions to sample
            
        Returns:
            Tuple of (states, actions, rewards, next_states, dones)
        """
        if len(self.buffer) < batch_size:
            batch_size = len(self.buffer)
            
        indices = random.sample(range(len(self.buffer)), batch_size)
        
        states = np.array([self.buffer[i][0] for i in indices])
        actions = np.array([self.buffer[i][1] for i in indices])
        rewards = np.array([self.buffer[i][2] for i in indices])
        next_states = np.array([self.buffer[i][3] for i in indices])
        dones = np.array([self.buffer[i][4] for i in indices])
        
        return states, actions, rewards, next_states, dones
    
    def sample_prioritized(self, batch_size: int, alpha: float = 0.6) -> Tuple:
        """
        Sample transitions using prioritized experience replay.
        
        Args:
            batch_size: Number of transitions to sample
            alpha: Priority exponent (0 = uniform, 1 = full priority)
            
        Returns:
            Tuple of (states, actions, rewards, next_states, dones, indices, weights)
        """
        if len(self.buffer) < batch_size:
            batch_size = len(self.buffer)
            
        priorities = np.array(self.priorities)
        probs = priorities ** alpha
        probs /= probs.sum()
        
        indices = np.random.choice(len(self.buffer), batch_size, p=probs, replace=False)
        
        states = np.array([self.buffer[i][0] for i in indices])
        actions = np.array([self.buffer[i][1] for i in indices])
        rewards = np.array([self.buffer[i][2] for i in indices])
        next_states = np.array([self.buffer[i][3] for i in indices])
        dones = np.array([self.buffer[i][4] for i in indices])
        
        # Compute importance sampling weights
        beta = getattr(self, "beta", 0.4)
        weights = (len(self.buffer) * probs[indices]) ** (-beta)
        weights /= weights.max()
        
        return states, actions, rewards, next_states, dones, indices, weights
    
    def __len__(self):
        """Return current size of buffer."""
        return len(self.buffer)
    
    def clear(self):
        """Clear all transitions from buffer."""
        self.buffer.clear()
        self.priorities.clear()
        
    def get_statistics(self) -> dict:
        """
        Get statistics about the buffer.
        
        Returns:
            Dictionary with buffer statistics
        """
        return {
            'size': len(self.buffer),
            'capacity': self.capacity,
            'full': len(self.buffer) >= self.capacity
        }


class PrioritizedReplayBuffer(ReplayBuffer):
    """
    Prioritized Experience Replay Buffer.
    
    Extends ReplayBuffer with prioritized sampling based on TD error.
    """
    
    def __init__(self, capacity: int = 100000, alpha: float = 0.6, beta: float = 0.4):
        """
        Initialize prioritized replay buffer.
        
        Args:
            capacity: Maximum number of transitions to store
            alpha: Priority exponent
            beta: Importance sampling exponent
        """
        super().__init__(capacity)
        self.alpha = alpha
        self.beta = beta
        self.priorities = np.ones(capacity)
        self.position = 0
        
    def push(self, state: np.ndarray, action: int, reward: float, 
             next_state: np.ndarray, done: bool, priority: float = None):
        """
        Add a transition with optional priority.
        
        If priority is None, use maximum priority.
        """
        if priority is None:
            priority = self.priorities.max() if len(self.buffer) > 0 else 1.0
            
        super().push(state, action, reward, next_state, done, priority)
        
    def update_priorities(self, indices: List[int], priorities: List[float]):
        """
        Update priorities for specific transitions.
        
        Args:
            indices: List of transition indices
            priorities: List of new priorities
        """
        for idx, priority in zip(indices, priorities):
            self.priorities[idx] = priority ** self.alpha


class MultiStepReplayBuffer:
    """
    Multi-step Replay Buffer for n-step returns.
    
    Stores transitions and computes n-step returns for better credit assignment.
    """
    
    def __init__(self, capacity: int = 100000, n_steps: int = 3, gamma: float = 0.99):
        """
        Initialize multi-step replay buffer.
        
        Args:
            capacity: Maximum number of transitions to store
            n_steps: Number of steps for multi-step return
            gamma: Discount factor
        """
        self.capacity = capacity
        self.n_steps = n_steps
        self.gamma = gamma
        self.buffer = deque(maxlen=capacity)
        self.n_step_buffer = deque(maxlen=n_steps)
        
    def push(self, state: np.ndarray, action: int, reward: float, 
             next_state: np.ndarray, done: bool):
        """
        Add a transition and compute n-step returns when possible.
        """
        self.n_step_buffer.append((state, action, reward, next_state, done))
        
        if len(self.n_step_buffer) < self.n_steps:
            return
            
        # Compute n-step return
        state, action, _, _, _ = self.n_step_buffer[0]
        
        n_step_reward = 0
        gamma_power = 1
        
        for i, (_, _, r, _, done) in enumerate(self.n_step_buffer):
            n_step_reward += gamma_power * r
            gamma_power *= self.gamma
            
            if done:
                break
                
        _, _, _, final_next_state, final_done = self.n_step_buffer[-1]
        
        self.buffer.append((state, action, n_step_reward, final_next_state, final_done))
        
    def sample(self, batch_size: int) -> Tuple:
        """Sample a batch of n-step transitions."""
        if len(self.buffer) < batch_size:
            batch_size = len(self.buffer)
            
        indices = random.sample(range(len(self.buffer)), batch_size)
        
        states = np.array([self.buffer[i][0] for i in indices])
        actions = np.array([self.buffer[i][1] for i in indices])
        rewards = np.array([self.buffer[i][2] for i in indices])
        next_states = np.array([self.buffer[i][3] for i in indices])
        dones = np.array([self.buffer[i][4] for i in indices])
        
        return states, actions, rewards, next_states, dones
    
    def __len__(self):
        return len(self.buffer)


# Standalone test
if __name__ == "__main__":
    # Test ReplayBuffer
    buffer = ReplayBuffer(capacity=1000)
    
    # Add some transitions
    for i in range(100):
        state = np.random.randn(18)
        action = np.random.randint(0, 9)
        reward = np.random.randn()
        next_state = np.random.randn(18)
        done = False
        buffer.push(state, action, reward, next_state, done)
        
    # Sample
    batch = buffer.sample(32)
    print(f"Buffer size: {len(buffer)}")
    print(f"Batch shapes: {[b.shape for b in batch]}")
    
    # Test PrioritizedReplayBuffer
    p_buffer = PrioritizedReplayBuffer(capacity=500)
    print("\nPrioritizedReplayBuffer created successfully")
    
    print("\n✅ Replay Buffer tests passed!")
