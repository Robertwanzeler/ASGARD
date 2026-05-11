"""
Actor Network for A3C (Asynchronous Advantage Actor-Critic)
=============================================================
Implementation of the Actor network for A3C algorithm.
Based on the paper's approach for policy-based learning in network slicing.

The Actor network learns the policy π(a|s) for selecting actions.
It outputs probability distribution over actions.

Author: GreenRAN Team - UFPA
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional


class ActorNetwork(nn.Module):
    """
    Actor Network for A3C.
    
    Architecture (based on paper):
        - Input: State features (18 dimensions)
        - Dense Layer 1: 256 units (ReLU)
        - Dense Layer 2: 128 units (ReLU)
        - Output Layer: num_actions (Softmax)
    
    The Actor learns the policy π(a|s) - probability of taking each action
    given the state. It is updated using policy gradient.
    """
    
    def __init__(self, 
                 state_size: int = 18,
                 hidden_size_1: int = 256,
                 hidden_size_2: int = 128,
                 num_actions: int = 9,
                 dropout: float = 0.2):
        """
        Initialize Actor network.
        
        Args:
            state_size: Number of input state features
            hidden_size_1: First hidden layer size
            hidden_size_2: Second hidden layer size
            num_actions: Number of possible actions
            dropout: Dropout probability
        """
        super(ActorNetwork, self).__init__()
        
        self.state_size = state_size
        self.hidden_size_1 = hidden_size_1
        self.hidden_size_2 = hidden_size_2
        self.num_actions = num_actions
        
        # First dense layer
        self.fc1 = nn.Linear(state_size, hidden_size_1)
        self.dropout1 = nn.Dropout(dropout)
        
        # Second dense layer
        self.fc2 = nn.Linear(hidden_size_1, hidden_size_2)
        self.dropout2 = nn.Dropout(dropout)
        
        # Output layer (policy)
        self.fc3 = nn.Linear(hidden_size_2, num_actions)
        
    def forward(self, state: torch.Tensor) -> torch.Tensor:
        """
        Forward pass through Actor network.
        
        Args:
            state: Input state tensor (batch_size, state_size)
            
        Returns:
            Action probabilities (batch_size, num_actions)
        """
        # First layer
        x = F.relu(self.fc1(state))
        x = self.dropout1(x)
        
        # Second layer
        x = F.relu(self.fc2(x))
        x = self.dropout2(x)
        
        # Output layer (policy)
        action_probs = F.softmax(self.fc3(x), dim=-1)
        
        return action_probs
    
    def forward_inference(self, state: torch.Tensor) -> torch.Tensor:
        """Forward pass for inference (no dropout, no batchnorm training mode)."""
        with torch.no_grad():
            x = F.relu(self.fc1(state))
            x = F.relu(self.fc2(x))
            action_probs = F.softmax(self.fc3(x), dim=-1)
        return action_probs
    def get_action(self, state: torch.Tensor, deterministic: bool = False) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Select action given state.
        
        Args:
            state: Input state
            deterministic: If True, select argmax; else sample
            
        Returns:
            Tuple of (selected_action, log_prob)
        """
        action_probs = self.forward(state)
        
        if deterministic:
            # Greedy selection
            action = torch.argmax(action_probs, dim=-1)
        else:
            # Sample from distribution
            distribution = torch.distributions.Categorical(action_probs)
            action = distribution.sample()
            
        # Get log probability for policy gradient
        log_prob = torch.log(action_probs.gather(1, action.unsqueeze(-1)) + 1e-8)
        
        return action, log_prob
    
    def evaluate_actions(self, state: torch.Tensor, actions: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Evaluate log probabilities and entropy for given actions.
        
        Args:
            state: Input states
            actions: Actions to evaluate
            
        Returns:
            Tuple of (log_probs, entropy)
        """
        action_probs = self.forward(state)
        
        # Log probabilities
        log_probs = torch.log(action_probs.gather(1, actions.unsqueeze(-1)) + 1e-8)
        
        # Entropy (for exploration bonus)
        entropy = -(action_probs * torch.log(action_probs + 1e-8)).sum(dim=-1)
        
        return log_probs, entropy


class ActorCriticNetwork(nn.Module):
    """
    Combined Actor-Critic Network for A3C.
    
    Shares feature extraction layers between Actor and Critic.
    More efficient than separate networks.
    """
    
    def __init__(self,
                 state_size: int = 18,
                 shared_hidden: int = 256,
                 actor_hidden: int = 128,
                 critic_hidden: int = 128,
                 num_actions: int = 9,
                 dropout: float = 0.2):
        """
        Initialize combined Actor-Critic network.
        
        Args:
            state_size: Input state dimension
            shared_hidden: Shared feature extraction layer size
            actor_hidden: Actor-specific layer size
            critic_hidden: Critic-specific layer size
            num_actions: Number of actions
            dropout: Dropout probability
        """
        super(ActorCriticNetwork, self).__init__()
        
        self.state_size = state_size
        self.num_actions = num_actions
        
        # Shared feature extraction
        self.shared = nn.Sequential(
            nn.Linear(state_size, shared_hidden),
            nn.ReLU(),
            nn.Dropout(dropout)
        )
        
        # Actor head
        self.actor = nn.Sequential(
            nn.Linear(shared_hidden, actor_hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(actor_hidden, num_actions),
            nn.Softmax(dim=-1)
        )
        
        # Critic head
        self.critic = nn.Sequential(
            nn.Linear(shared_hidden, critic_hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(critic_hidden, 1)  # Value function V(s)
        )
        
    def forward(self, state: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass returning both policy and value.
        
        Args:
            state: Input state
            
        Returns:
            Tuple of (action_probs, state_value)
        """
        shared_features = self.shared(state)
        
        action_probs = self.actor(shared_features)
        state_value = self.critic(shared_features)
        
        return action_probs, state_value
    
    def get_action(self, state: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Get action, log probability, and state value.
        
        Args:
            state: Input state
            
        Returns:
            Tuple of (action, log_prob, state_value)
        """
        action_probs, state_value = self.forward(state)
        
        # Sample action
        distribution = torch.distributions.Categorical(action_probs)
        action = distribution.sample()
        log_prob = distribution.log_prob(action)
        
        return action, log_prob, state_value


class DistributedActor:
    """
    Distributed Actor for A3C training.
    
    Each worker has its own actor that computes gradients locally
    and asynchronously updates the global network.
    """
    
    def __init__(self, 
                 state_size: int = 18,
                 num_actions: int = 9,
                 actor_lr: float = 1e-4,
                 entropy_coef: float = 0.01,
                 value_loss_coef: float = 0.5,
                 max_grad_norm: float = 0.5):
        """
        Initialize Distributed Actor.
        
        Args:
            state_size: State dimension
            num_actions: Number of actions
            actor_lr: Learning rate
            entropy_coef: Entropy coefficient for exploration
            value_loss_coef: Value loss coefficient
            max_grad_norm: Maximum gradient norm for clipping
        """
        self.local_network = ActorNetwork(state_size=state_size, num_actions=num_actions)
        self.optimizer = torch.optim.Adam(self.local_network.parameters(), lr=actor_lr)
        
        self.entropy_coef = entropy_coef
        self.value_loss_coef = value_loss_coef
        self.max_grad_norm = max_grad_norm
        
    def update(self, 
               states: torch.Tensor,
               actions: torch.Tensor,
               rewards: torch.Tensor,
               next_states: torch.Tensor,
               dones: torch.Tensor,
               values: torch.Tensor,
               gamma: float = 0.99) -> dict:
        """
        Update actor using A3C algorithm.
        
        Args:
            states: Batch of states
            actions: Batch of actions
            rewards: Batch of rewards
            next_states: Batch of next states
            dones: Batch of done flags
            values: Old value estimates
            gamma: Discount factor
            
        Returns:
            Dictionary of loss values
        """
        # Get current action probabilities and values
        action_probs = self.local_network(states)
        
        # Compute log probabilities
        log_probs = torch.log(action_probs.gather(1, actions.unsqueeze(-1)) + 1e-8)
        
        # Compute entropy for exploration bonus
        entropy = -(action_probs * torch.log(action_probs + 1e-8)).sum(dim=-1)
        
        # Compute advantage (simplified - using rewards as advantage)
        advantage = rewards - values.detach()
        
        # Policy gradient loss
        policy_loss = -(log_probs.squeeze() * advantage.detach()).mean()
        
        # Value loss (optional, typically handled by critic)
        value_loss = F.mse_loss(values, rewards)
        
        # Total loss
        total_loss = policy_loss - self.entropy_coef * entropy.mean() + self.value_loss_coef * value_loss
        
        # Update
        self.optimizer.zero_grad()
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.local_network.parameters(), self.max_grad_norm)
        self.optimizer.step()
        
        return {
            'policy_loss': policy_loss.item(),
            'value_loss': value_loss.item(),
            'entropy': entropy.mean().item(),
            'total_loss': total_loss.item()
        }
    
    def sync_from(self, global_network: nn.Module):
        """Copy weights from global network."""
        self.local_network.load_state_dict(global_network.state_dict())


def create_actor_network(config: dict) -> ActorNetwork:
    """
    Factory function to create Actor network from config.
    
    Args:
        config: Dictionary with configuration
        
    Returns:
        ActorNetwork instance
    """
    return ActorNetwork(
        state_size=config.get('state_size', 18),
        hidden_size_1=config.get('actor_hidden_1', 256),
        hidden_size_2=config.get('actor_hidden_2', 128),
        num_actions=config.get('num_actions', 9),
        dropout=config.get('dropout', 0.2)
    )


# Standalone test
if __name__ == "__main__":
    # Create Actor network
    actor = ActorNetwork(
        state_size=18,
        num_actions=9
    )
    
    print("=" * 60)
    print("Actor Network Test")
    print("=" * 60)
    
    # Print architecture
    print("\nArchitecture:")
    print(actor)
    
    # Count parameters
    total_params = sum(p.numel() for p in actor.parameters())
    print(f"\nTotal parameters: {total_params:,}")
    
    # Test forward pass
    batch_size = 32
    state = torch.randn(batch_size, 18)
    
    print(f"\nInput shape: {state.shape}")
    
    # Forward pass
    action_probs = actor(state)
    print(f"Output shape: {action_probs.shape}")
    print(f"Sum of probabilities: {action_probs.sum(dim=1)}")
    
    # Test action selection
    action, log_prob = actor.get_action(state)
    print(f"Selected action shape: {action.shape}")
    print(f"Log probability shape: {log_prob.shape}")
    
    # Test evaluation
    actions = torch.randint(0, 9, (batch_size,))
    log_probs, entropy = actor.evaluate_actions(state, actions)
    print(f"Evaluated log_probs shape: {log_probs.shape}")
    print(f"Entropy shape: {entropy.shape}")
    print(f"Mean entropy: {entropy.mean().item():.4f}")
    
    # Test Actor-Critic combined network
    print("\n" + "=" * 60)
    print("Actor-Critic Combined Network Test")
    print("=" * 60)
    
    ac_network = ActorCriticNetwork(state_size=18, num_actions=9)
    state = torch.randn(32, 18)
    action_probs, state_value = ac_network(state)
    
    print(f"Action probabilities shape: {action_probs.shape}")
    print(f"State value shape: {state_value.shape}")
    
    # Test DistributedActor
    print("\n" + "=" * 60)
    print("Distributed Actor Test")
    print("=" * 60)
    
    dist_actor = DistributedActor(state_size=18, num_actions=9)
    print("DistributedActor created successfully")
    
    # Simulate update
    states = torch.randn(32, 18)
    actions = torch.randint(0, 9, (32,))
    rewards = torch.randn(32)
    next_states = torch.randn(32, 18)
    dones = torch.zeros(32)
    values = torch.randn(32)
    
    losses = dist_actor.update(states, actions, rewards, next_states, dones, values)
    print(f"Update losses: {losses}")
    
    print("\n✅ Actor network tests passed!")