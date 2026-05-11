"""
Critic Network for A3C (Asynchronous Advantage Actor-Critic)
=============================================================
Implementation of the Critic network for A3C algorithm.
Based on the paper's approach for value-based learning in network slicing.

The Critic network learns the value function V(s) - expected return from state s.
It is used to compute the advantage A(s, a) = Q(s, a) - V(s).

Author: GreenRAN Team - UFPA
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional


class CriticNetwork(nn.Module):
    """
    Critic Network for A3C.
    
    Architecture (based on paper):
        - Input: State features (18 dimensions)
        - Dense Layer 1: 256 units (ReLU)
        - Dense Layer 2: 128 units (ReLU)
        - Output Layer: 1 (State Value V(s))
    
    The Critic estimates the value function V(s) which is used
    to compute the advantage for policy gradient updates.
    """
    
    def __init__(self, 
                 state_size: int = 18,
                 hidden_size_1: int = 256,
                 hidden_size_2: int = 128,
                 dropout: float = 0.2):
        """
        Initialize Critic network.
        
        Args:
            state_size: Number of input state features
            hidden_size_1: First hidden layer size
            hidden_size_2: Second hidden layer size
            dropout: Dropout probability
        """
        super(CriticNetwork, self).__init__()
        
        self.state_size = state_size
        self.hidden_size_1 = hidden_size_1
        self.hidden_size_2 = hidden_size_2
        
        # First dense layer
        self.fc1 = nn.Linear(state_size, hidden_size_1)
        self.dropout1 = nn.Dropout(dropout)
        
        # Second dense layer
        self.fc2 = nn.Linear(hidden_size_1, hidden_size_2)
        self.dropout2 = nn.Dropout(dropout)
        
        # Output layer (value)
        self.fc3 = nn.Linear(hidden_size_2, 1)
        
    def forward(self, state: torch.Tensor) -> torch.Tensor:
        """
        Forward pass through Critic network.
        
        Args:
            state: Input state tensor (batch_size, state_size)
            
        Returns:
            State value (batch_size, 1)
        """
        # First layer
        x = F.relu(self.fc1(state))
        x = self.dropout1(x)
        
        # Second layer
        x = F.relu(self.fc2(x))
        x = self.dropout2(x)
        
        # Output layer (value)
        value = self.fc3(x)
        
        return value
    
    def forward_inference(self, state: torch.Tensor) -> torch.Tensor:
        """Forward pass for inference (no dropout, no batchnorm training mode)."""
        with torch.no_grad():
            x = F.relu(self.fc1(state))
            x = F.relu(self.fc2(x))
            value = self.fc3(x)
        return value
    
    def get_value(self, state: torch.Tensor) -> torch.Tensor:
        """
        Get state value without gradient tracking.
        
        Args:
            state: Input state
            
        Returns:
            State value (detached)
        """
        with torch.no_grad():
            return self.forward(state)


class DoubleCriticNetwork(nn.Module):
    """
    Double Critic Network for reducing overestimation.
    
    Uses two separate critic networks and takes the minimum value
    for more stable learning (similar to Double DQN).
    """
    
    def __init__(self, 
                 state_size: int = 18,
                 hidden_size: int = 256,
                 dropout: float = 0.2):
        """
        Initialize double critic network.
        
        Args:
            state_size: State dimension
            hidden_size: Hidden layer size
            dropout: Dropout probability
        """
        super(DoubleCriticNetwork, self).__init__()
        
        # Two critic networks
        self.critic1 = CriticNetwork(state_size, hidden_size, hidden_size // 2, dropout)
        self.critic2 = CriticNetwork(state_size, hidden_size, hidden_size // 2, dropout)
        
    def forward(self, state: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass through both critics.
        
        Args:
            state: Input state
            
        Returns:
            Tuple of (value1, value2)
        """
        v1 = self.critic1(state)
        v2 = self.critic2(state)
        return v1, v2
    
    def get_min_value(self, state: torch.Tensor) -> torch.Tensor:
        """
        Get minimum value from both critics.
        
        Args:
            state: Input state
            
        Returns:
            Minimum value (for more stable learning)
        """
        v1, v2 = self.forward(state)
        return torch.min(v1, v2)


class DistributedCritic:
    """
    Distributed Critic for A3C training.
    
    Each worker has its own critic that computes value estimates
    and gradients locally for asynchronous updates.
    """
    
    def __init__(self, 
                 state_size: int = 18,
                 critic_lr: float = 2e-4,
                 gamma: float = 0.99,
                 value_loss_coef: float = 0.5,
                 max_grad_norm: float = 0.5):
        """
        Initialize Distributed Critic.
        
        Args:
            state_size: State dimension
            critic_lr: Learning rate
            gamma: Discount factor
            value_loss_coef: Value loss coefficient
            max_grad_norm: Maximum gradient norm
        """
        self.local_network = CriticNetwork(state_size=state_size)
        self.optimizer = torch.optim.Adam(self.local_network.parameters(), lr=critic_lr)
        
        self.gamma = gamma
        self.value_loss_coef = value_loss_coef
        self.max_grad_norm = max_grad_norm
        
    def compute_advantage(self, 
                          rewards: torch.Tensor,
                          values: torch.Tensor,
                          next_values: torch.Tensor,
                          dones: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Compute advantage using TD error.
        
        Advantage = R + γ * V(s') - V(s)
        
        Args:
            rewards: Rewards
            values: Current value estimates
            next_values: Next state values
            dones: Done flags
            
        Returns:
            Tuple of (returns, advantages)
        """
        # Compute TD targets
        targets = rewards + self.gamma * next_values * (1 - dones)
        
        # Advantages (TD error)
        advantages = targets - values.detach()
        
        return targets, advantages
    
    def update(self, 
               states: torch.Tensor,
               targets: torch.Tensor,
               advantages: torch.Tensor) -> dict:
        """
        Update critic using MSE loss.
        
        Args:
            states: Batch of states
            targets: TD targets
            advantages: Advantages (for logging)
            
        Returns:
            Dictionary of loss values
        """
        # Get value estimates
        values = self.local_network(states)
        
        # Value loss (MSE)
        value_loss = F.mse_loss(values.squeeze(), targets)
        
        # Total loss
        total_loss = self.value_loss_coef * value_loss
        
        # Update
        self.optimizer.zero_grad()
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.local_network.parameters(), self.max_grad_norm)
        self.optimizer.step()
        
        return {
            'value_loss': value_loss.item(),
            'mean_value': values.mean().item(),
            'mean_target': targets.mean().item()
        }
    
    def sync_from(self, global_network: nn.Module):
        """Copy weights from global network."""
        self.local_network.load_state_dict(global_network.state_dict())


def create_critic_network(config: dict) -> CriticNetwork:
    """
    Factory function to create Critic network from config.
    
    Args:
        config: Dictionary with configuration
        
    Returns:
        CriticNetwork instance
    """
    return CriticNetwork(
        state_size=config.get('state_size', 18),
        hidden_size_1=config.get('critic_hidden_1', 256),
        hidden_size_2=config.get('critic_hidden_2', 128),
        dropout=config.get('dropout', 0.2)
    )


# Standalone test
if __name__ == "__main__":
    # Create Critic network
    critic = CriticNetwork(
        state_size=18,
        hidden_size_1=256,
        hidden_size_2=128
    )
    
    print("=" * 60)
    print("Critic Network Test")
    print("=" * 60)
    
    # Print architecture
    print("\nArchitecture:")
    print(critic)
    
    # Count parameters
    total_params = sum(p.numel() for p in critic.parameters())
    print(f"\nTotal parameters: {total_params:,}")
    
    # Test forward pass
    batch_size = 32
    state = torch.randn(batch_size, 18)
    
    print(f"\nInput shape: {state.shape}")
    
    # Forward pass
    value = critic(state)
    print(f"Output shape: {value.shape}")
    print(f"Value range: [{value.min():.3f}, {value.max():.3f}]")
    
    # Test value extraction
    value_detached = critic.get_value(state)
    print(f"Detached value shape: {value_detached.shape}")
    
    # Test DistributedCritic
    print("\n" + "=" * 60)
    print("Distributed Critic Test")
    print("=" * 60)
    
    dist_critic = DistributedCritic(state_size=18)
    print("DistributedCritic created successfully")
    
    # Simulate update
    states = torch.randn(32, 18)
    rewards = torch.randn(32)
    values = torch.randn(32)
    next_values = torch.randn(32)
    dones = torch.zeros(32)
    
    targets, advantages = dist_critic.compute_advantage(rewards, values, next_values, dones)
    print(f"Targets shape: {targets.shape}")
    print(f"Advantages shape: {advantages.shape}")
    
    losses = dist_critic.update(states, targets, advantages)
    print(f"Update losses: {losses}")
    
    # Test DoubleCritic
    print("\n" + "=" * 60)
    print("Double Critic Test")
    print("=" * 60)
    
    double_critic = DoubleCriticNetwork(state_size=18)
    v1, v2 = double_critic(state)
    print(f"Critic 1 value: {v1.mean():.3f}")
    print(f"Critic 2 value: {v2.mean():.3f}")
    
    v_min = double_critic.get_min_value(state)
    print(f"Min value: {v_min.mean():.3f}")
    
    print("\n✅ Critic network tests passed!")