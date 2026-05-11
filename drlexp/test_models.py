#!/usr/bin/env python3
"""
Quick test for DRL models without external dependencies
Tests the PyTorch models that don't require gymnasium
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import torch
import numpy as np

def test_sbilstm():
    """Test SBiLSTM model"""
    print("=" * 60)
    print("Testing SBiLSTM Model")
    print("=" * 60)
    
    from drl.models.sbilstm import SBiLSTM
    
    model = SBiLSTM(input_size=18, hidden_size_1=128, hidden_size_2=64)
    
    # Test forward
    x = torch.randn(4, 1200, 18)  # batch, seq, features
    out, hidden = model(x)
    
    print(f"Input shape: {x.shape}")
    print(f"Output shape: {out.shape}")
    print(f"✅ SBiLSTM test passed!")
    return True


def test_actor():
    """Test Actor network"""
    print("\n" + "=" * 60)
    print("Testing Actor Network")
    print("=" * 60)
    
    from drl.models.actor import ActorNetwork
    
    actor = ActorNetwork(state_size=18, num_actions=9)
    
    # Test forward
    x = torch.randn(4, 18)
    probs = actor(x)
    
    print(f"Input shape: {x.shape}")
    print(f"Output shape: {probs.shape}")
    print(f"✅ Actor test passed!")
    return True


def test_critic():
    """Test Critic network"""
    print("\n" + "=" * 60)
    print("Testing Critic Network")
    print("=" * 60)
    
    from drl.models.critic import CriticNetwork
    
    critic = CriticNetwork(state_size=18)
    
    # Test forward
    x = torch.randn(4, 18)
    value = critic(x)
    
    print(f"Input shape: {x.shape}")
    print(f"Output shape: {value.shape}")
    print(f"✅ Critic test passed!")
    return True


def test_replay_buffer():
    """Test ReplayBuffer"""
    print("\n" + "=" * 60)
    print("Testing Replay Buffer")
    print("=" * 60)
    
    from drl.replay_buffer import ReplayBuffer
    
    buffer = ReplayBuffer(capacity=1000)
    
    # Add transitions
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
    print(f"✅ ReplayBuffer test passed!")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("GreenRAN DRL - Quick Tests (PyTorch Only)")
    print("=" * 60 + "\n")
    
    # Check PyTorch
    print(f"PyTorch version: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}\n")
    
    # Run tests
    tests = [
        ("SBiLSTM", test_sbilstm),
        ("Actor", test_actor),
        ("Critic", test_critic),
        ("Replay Buffer", test_replay_buffer),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            if test_fn():
                passed += 1
        except Exception as e:
            print(f"❌ {name} test failed: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    if failed == 0:
        print("\n✅ All PyTorch model tests passed!")
        print("\nNOTE: Gymnasium environment requires: pip install gymnasium")
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)