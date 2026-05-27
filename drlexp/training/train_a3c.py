#!/usr/bin/env python3
"""
Legacy A3C Training Script
==========================
Training script for the legacy A3C (Asynchronous Advantage Actor-Critic) line.
Kept for reproducibility while the platform migrates to SAC for AI/RAN shared
resource allocation.

This script:
1. Creates the Gymnasium environment
2. Initializes global Actor-Critic networks
3. Launches multiple worker processes
4. Performs asynchronous updates to global network

Author: GreenRAN Team - UFPA
"""

import os
import sys
import time
import json
import threading
import multiprocessing as mp
from datetime import datetime
from collections import deque
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.multiprocessing import Process, Queue

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from drl.gym_environment import GreenRANGymEnv
from drl.models.actor import ActorNetwork
from drl.models.critic import CriticNetwork
from drl.replay_buffer import ReplayBuffer


def write_json(path: str, payload: dict | list) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
        f.write('\n')


# Global configuration
CONFIG = {
    'num_workers': 8,
    'num_episodes': 10000,
    'max_steps_per_episode': 1000,
    'gamma': 0.99,              # Discount factor
    'actor_lr': 1e-4,           # Actor learning rate
    'critic_lr': 2e-4,          # Critic learning rate
    'entropy_coef': 0.01,       # Entropy coefficient
    'value_loss_coef': 0.5,    # Value loss coefficient
    'max_grad_norm': 0.5,       # Gradient clipping
    'update_frequency': 32,     # Steps between updates
    'state_size': 18,
    'num_actions': 9,
    'log_interval': 10,
    'save_interval': 100,
    'db_path': '/tmp/rapp_data_lake.db',
    'model_dir': './models/a3c'
}


class A3CAgent:
    """
    A3C Agent implementation.
    
    Coordinates between global networks and worker processes.
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize A3C Agent.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or CONFIG
        
        # Create directories
        os.makedirs(self.config['model_dir'], exist_ok=True)
        
        # Initialize global networks
        self.global_actor = ActorNetwork(
            state_size=self.config['state_size'],
            num_actions=self.config['num_actions']
        )
        self.global_critic = CriticNetwork(
            state_size=self.config['state_size']
        )
        
        # Optimizers
        self.actor_optimizer = optim.Adam(
            self.global_actor.parameters(), 
            lr=self.config['actor_lr']
        )
        self.critic_optimizer = optim.Adam(
            self.global_critic.parameters(), 
            lr=self.config['critic_lr']
        )
        
        # Training tracking
        self.episode_count = 0
        self.global_step = 0
        self.reward_history = deque(maxlen=100)
        
    def save_models(self, path: str = None):
        """Save model checkpoints."""
        if path is None:
            path = self.config['model_dir']
            
        torch.save({
            'actor': self.global_actor.state_dict(),
            'critic': self.global_critic.state_dict(),
            'actor_optimizer': self.actor_optimizer.state_dict(),
            'critic_optimizer': self.critic_optimizer.state_dict(),
            'episode': self.episode_count,
            'step': self.global_step
        }, os.path.join(path, 'checkpoint.pt'))
        
        print(f"[A3C] Models saved to {path}")
        
    def load_models(self, path: str):
        """Load model checkpoints."""
        if os.path.exists(path):
            checkpoint = torch.load(path)
            self.global_actor.load_state_dict(checkpoint['actor'])
            self.global_critic.load_state_dict(checkpoint['critic'])
            self.actor_optimizer.load_state_dict(checkpoint['actor_optimizer'])
            self.critic_optimizer.load_state_dict(checkpoint['critic_optimizer'])
            self.episode_count = checkpoint.get('episode', 0)
            self.global_step = checkpoint.get('step', 0)
            print(f"[A3C] Models loaded from {path}")
        else:
            print(f"[A3C] No checkpoint found at {path}")


class A3CWorker(Process):
    """
    A3C Worker Process.
    
    Each worker runs its own environment and computes gradients locally.
    """
    
    def __init__(self, 
                 worker_id: int,
                 global_actor: nn.Module,
                 global_critic: nn.Module,
                 config: dict,
                 result_queue: Queue):
        """
        Initialize A3C worker.
        
        Args:
            worker_id: Worker identifier
            global_actor: Global actor network (shared)
            global_critic: Global critic network (shared)
            config: Configuration dictionary
            result_queue: Queue for sending results to main process
        """
        super().__init__()
        self.worker_id = worker_id
        self.global_actor = global_actor
        self.global_critic = global_critic
        self.config = config
        self.result_queue = result_queue
        
    def run(self):
        """Run the worker process."""
        # Set random seed
        np.random.seed(self.worker_id)
        torch.manual_seed(self.worker_id)
        
        # Create local networks (copies of global)
        local_actor = ActorNetwork(
            state_size=self.config['state_size'],
            num_actions=self.config['num_actions']
        )
        local_critic = CriticNetwork(
            state_size=self.config['state_size']
        )
        
        # Set to eval mode to avoid BatchNorm issues with batch size 1
        local_actor.eval()
        local_critic.eval()
        
        # Set to eval mode to avoid BatchNorm issues with batch size 1
        local_actor.eval()
        local_critic.eval()
        
        # Optimizers for local networks
        actor_optimizer = optim.Adam(local_actor.parameters(), lr=self.config['actor_lr'])
        critic_optimizer = optim.Adam(local_critic.parameters(), lr=self.config['critic_lr'])
        
        # Create environment
        env = GreenRANGymEnv(db_path=self.config['db_path'])
        
        print(f"[Worker {self.worker_id}] Started")
        
        episode_rewards = []
        
        for episode in range(self.config['num_episodes']):
            # Reset environment
            state, _ = env.reset()
            state = torch.FloatTensor(state)
            
            episode_reward = 0
            episode_steps = 0
            
            # Storage for trajectory
            states = []
            actions = []
            rewards = []
            values = []
            dones = []
            
            while episode_steps < self.config['max_steps_per_episode']:
                # Get action from local actor
                action_probs = local_actor(state.unsqueeze(0))
                action_dist = torch.distributions.Categorical(action_probs)
                action = action_dist.sample().item()
                
                # Get value from local critic
                value = local_critic(state.unsqueeze(0)).item()
                
                # Take action in environment
                next_state, reward, terminated, truncated, info = env.step(action)
                next_state = torch.FloatTensor(next_state)
                
                # Store transition
                states.append(state)
                actions.append(action)
                rewards.append(reward)
                values.append(value)
                dones.append(terminated)
                
                # Update state
                state = next_state
                episode_reward += reward
                episode_steps += 1
                
                # Check if done or time to update
                if terminated or truncated or episode_steps % self.config['update_frequency'] == 0:
                    # Compute returns and advantages
                    with torch.no_grad():
                        if terminated or truncated:
                            next_value = 0
                        else:
                            next_value = local_critic(state.unsqueeze(0)).item()
                    
                    # Compute GAE (Generalized Advantage Estimation)
                    returns = []
                    advantages = []
                    gae = 0
                    
                    for t in reversed(range(len(rewards))):
                        if t == len(rewards) - 1:
                            next_val = next_value
                        else:
                            next_val = values[t + 1]
                            
                        delta = rewards[t] + self.config['gamma'] * next_val * (1 - dones[t]) - values[t]
                        gae = delta + self.config['gamma'] * 0.95 * (1 - dones[t]) * gae
                        advantages.insert(0, gae)
                        returns.insert(0, rewards[t] + self.config['gamma'] * next_val * (1 - dones[t]))
                    
                    # Convert to tensors
                    states_batch = torch.stack(states)
                    actions_batch = torch.LongTensor(actions)
                    returns_batch = torch.FloatTensor(returns)
                    advantages_batch = torch.FloatTensor(advantages)
                    
                    # Normalize advantages
                    advantages_batch = (advantages_batch - advantages_batch.mean()) / (advantages_batch.std() + 1e-8)
                    
                    # Update local networks
                    # Actor update
                    action_probs = local_actor(states_batch)
                    log_probs = torch.log(action_probs.gather(1, actions_batch.unsqueeze(-1)) + 1e-8)
                    actor_loss = -(log_probs.squeeze() * advantages_batch.detach()).mean()
                    
                    # Entropy bonus
                    entropy = -(action_probs * torch.log(action_probs + 1e-8)).sum(dim=-1).mean()
                    
                    actor_loss = actor_loss - self.config['entropy_coef'] * entropy
                    
                    actor_optimizer.zero_grad()
                    actor_loss.backward()
                    torch.nn.utils.clip_grad_norm_(local_actor.parameters(), self.config['max_grad_norm'])
                    
                    # Copy gradients to global (simplified - in real impl would use multiprocessing)
                    for global_param, local_param in zip(self.global_actor.parameters(), local_actor.parameters()):
                        if local_param.grad is not None:
                            global_param._grad = local_param.grad.clone()
                    
                    actor_optimizer.step()
                    
                    # Critic update
                    values_pred = local_critic(states_batch).squeeze()
                    critic_loss = F.mse_loss(values_pred, returns_batch)
                    
                    critic_optimizer.zero_grad()
                    critic_loss.backward()
                    torch.nn.utils.clip_grad_norm_(local_critic.parameters(), self.config['max_grad_norm'])
                    
                    for global_param, local_param in zip(self.global_critic.parameters(), local_critic.parameters()):
                        if local_param.grad is not None:
                            global_param._grad = local_param.grad.clone()
                    
                    critic_optimizer.step()
                    
                    # Sync local networks with global
                    local_actor.load_state_dict(self.global_actor.state_dict())
                    local_critic.load_state_dict(self.global_critic.state_dict())
                    
                    # Clear trajectory
                    states = []
                    actions = []
                    rewards = []
                    values = []
                    dones = []
                
                if terminated or truncated:
                    break
            
            episode_rewards.append(episode_reward)
            
            # Send statistics to main process
            if episode % self.config['log_interval'] == 0:
                mean_reward = np.mean(episode_rewards[-10:])
                self.result_queue.put({
                    'worker': self.worker_id,
                    'episode': episode,
                    'reward': episode_reward,
                    'mean_reward_10': mean_reward
                })
        
        env.close()
        print(f"[Worker {self.worker_id}] Finished")


def train_a3c(config: dict = None):
    """
    Main training function for A3C.
    
    Args:
        config: Configuration dictionary
    """
    config = config or CONFIG
    
    print("=" * 60)
    print("A3C Training - GreenRAN Resource Allocation")
    print("=" * 60)
    print(f"Workers: {config['num_workers']}")
    print(f"Episodes: {config['num_episodes']}")
    print(f"State size: {config['state_size']}")
    print(f"Action space: {config['num_actions']}")
    print()
    
    # Create agent
    agent = A3CAgent(config)
    
    # Result queue
    result_queue = mp.Queue()
    history_records = []

    def persist_history() -> None:
        history_json = os.path.join(config['model_dir'], 'a3c_training_history.json')
        history_csv = os.path.join(config['model_dir'], 'a3c_training_history.csv')
        summary_json = os.path.join(config['model_dir'], 'a3c_training_summary.json')
        write_json(history_json, history_records)
        if history_records:
            import pandas as pd
            pd.DataFrame(history_records).to_csv(history_csv, index=False)
            mean_reward = float(np.mean([row['reward'] for row in history_records]))
            max_reward = float(np.max([row['reward'] for row in history_records]))
            min_reward = float(np.min([row['reward'] for row in history_records]))
        else:
            open(history_csv, 'w', encoding='utf-8').write('episode,worker,reward,mean_reward_10,elapsed_s\n')
            mean_reward = max_reward = min_reward = 0.0
        write_json(summary_json, {
            'model': 'A3C',
            'episodes_requested': int(config['num_episodes']),
            'logged_points': len(history_records),
            'mean_logged_reward': mean_reward,
            'max_logged_reward': max_reward,
            'min_logged_reward': min_reward,
            'history_json': history_json,
            'history_csv': history_csv,
        })
    
    # Create workers
    workers = []
    for i in range(config['num_workers']):
        worker = A3CWorker(
            worker_id=i,
            global_actor=agent.global_actor,
            global_critic=agent.global_critic,
            config=config,
            result_queue=result_queue
        )
        worker.start()
        workers.append(worker)
        time.sleep(0.1)  # Small delay between workers
    
    # Collect results
    start_time = time.time()
    try:
        while agent.episode_count < config['num_episodes']:
            if not result_queue.empty():
                result = result_queue.get()
                
                if result['episode'] % config['log_interval'] == 0:
                    elapsed = time.time() - start_time
                    print(f"[{elapsed:.1f}s] Worker {result['worker']} Episode {result['episode']}: "
                          f"Reward={result['reward']:.2f}, Mean_10={result['mean_reward_10']:.2f}")
                    history_records.append({
                        'episode': int(result['episode']),
                        'worker': int(result['worker']),
                        'reward': float(result['reward']),
                        'mean_reward_10': float(result['mean_reward_10']),
                        'elapsed_s': float(elapsed),
                    })
                    
                    agent.episode_count += 1
                    
                    # Save periodically
                    if agent.episode_count % config['save_interval'] == 0:
                        agent.save_models()
                        persist_history()
                        
    except KeyboardInterrupt:
        print("\n[A3C] Training interrupted by user")
        
    finally:
        # Stop workers
        for worker in workers:
            worker.terminate()
            worker.join()
            
        # Save final models
        agent.save_models()
        persist_history()
        
        elapsed = time.time() - start_time
        print(f"\n[A3C] Training completed in {elapsed:.1f}s")
        print(f"[A3C] Total episodes: {agent.episode_count}")


def evaluate_a3c(model_path: str, num_episodes: int = 100):
    """
    Evaluate trained A3C model.
    
    Args:
        model_path: Path to saved model
        num_episodes: Number of episodes to evaluate
    """
    print("=" * 60)
    print("A3C Model Evaluation")
    print("=" * 60)
    
    # Load model
    agent = A3CAgent(CONFIG)
    agent.load_models(model_path)
    
    # Create environment
    env = GreenRANGymEnv(db_path=CONFIG['db_path'])
    
    # Evaluate
    episode_rewards = []
    episode_lengths = []
    
    for episode in range(num_episodes):
        state, _ = env.reset()
        state = torch.FloatTensor(state)
        
        episode_reward = 0
        episode_steps = 0
        
        while episode_steps < CONFIG['max_steps_per_episode']:
            # Greedy action
            action_probs = agent.global_actor(state.unsqueeze(0))
            action = torch.argmax(action_probs, dim=-1).item()
            
            next_state, reward, terminated, truncated, info = env.step(action)
            next_state = torch.FloatTensor(next_state)
            
            state = next_state
            episode_reward += reward
            episode_steps += 1
            
            if terminated or truncated:
                break
        
        episode_rewards.append(episode_reward)
        episode_lengths.append(episode_steps)
        
        if episode % 10 == 0:
            print(f"Episode {episode}: Reward={episode_reward:.2f}, Steps={episode_steps}")
    
    env.close()
    
    # Print statistics
    print("\n" + "=" * 60)
    print("Evaluation Results")
    print("=" * 60)
    print(f"Mean Reward: {np.mean(episode_rewards):.2f}")
    print(f"Std Reward: {np.std(episode_rewards):.2f}")
    print(f"Mean Episode Length: {np.mean(episode_lengths):.1f}")
    print(f"Min Reward: {np.min(episode_rewards):.2f}")
    print(f"Max Reward: {np.max(episode_rewards):.2f}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='A3C Training for GreenRAN')
    parser.add_argument('--mode', type=str, default='train', choices=['train', 'eval'],
                        help='Mode: train or evaluate')
    parser.add_argument('--model', type=str, default=None,
                        help='Path to model for evaluation')
    parser.add_argument('--episodes', type=int, default=500,
                        help='Number of episodes for training')
    parser.add_argument('--workers', type=int, default=8,
                        help='Number of workers')
    parser.add_argument('--db', type=str, default='/tmp/rapp_data_lake.db',
                        help='Path to data lake')
    
    args = parser.parse_args()
    
    # Update config
    CONFIG['num_workers'] = args.workers
    CONFIG['db_path'] = args.db
    
    if args.mode == 'train':
        if args.episodes:
            CONFIG['num_episodes'] = args.episodes
        train_a3c(CONFIG)
    else:
        if args.model is None:
            print("Error: --model required for evaluation")
        else:
            evaluate_a3c(args.model, args.episodes)
