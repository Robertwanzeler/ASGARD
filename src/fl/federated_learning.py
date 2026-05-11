#!/usr/bin/env python3
"""
GreenRAN - Federated Learning Module
=====================================
Federated learning for distributed xApps
"""

import os
import json
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class FLClientConfig:
    """Federated Learning client configuration"""
    client_id: str
    model_type: str  # "classifier", "regressor"
    local_epochs: int = 5
    batch_size: int = 32
    learning_rate: float = 0.01

@dataclass
class ModelUpdate:
    """Model update from a client"""
    client_id: str
    timestamp: float
    num_samples: int
    weights_delta: Dict[str, np.ndarray]
    metrics: Dict[str, float]

class FLClient:
    """Federated Learning client (runs on each xApp)"""
    
    def __init__(self, config: FLClientConfig):
        self.config = config
        self.local_model = None
        self.local_data = []
    
    def receive_global_model(self, global_weights: Dict[str, np.ndarray]):
        """Receive global model from aggregator"""
        self.local_model = global_weights.copy()
    
    def local_train(self, data: np.ndarray, labels: np.ndarray) -> Dict:
        """Train locally on client data"""
        # Placeholder: would implement actual local training
        # In production, would use actual ML training
        
        num_batches = len(data) // self.config.batch_size
        
        return {
            'client_id': self.config.client_id,
            'epochs': self.config.local_epochs,
            'batches': num_batches,
            'accuracy': 0.88
        }
    
    def get_model_update(self) -> ModelUpdate:
        """Get model update to send to aggregator"""
        # Placeholder: would compute actual weights delta
        
        weights_delta = {
            'layer1': np.random.randn(10, 10) * 0.01,
            'layer2': np.random.randn(10, 5) * 0.01
        }
        
        return ModelUpdate(
            client_id=self.config.client_id,
            timestamp=datetime.now().timestamp(),
            num_samples=len(self.local_data),
            weights_delta=weights_delta,
            metrics={'accuracy': 0.88, 'loss': 0.12}
        )


class FLServer:
    """Federated Learning server (runs on rApp)"""
    
    def __init__(self, num_clients: int = 3):
        self.num_clients = num_clients
        self.global_model = None
        self.global_round = 0
        self.client_updates = []
        self.history = []
    
    def initialize_model(self, model_shape: Dict[str, Tuple]):
        """Initialize global model"""
        self.global_model = {
            name: np.random.randn(*shape) * 0.01 
            for name, shape in model_shape.items()
        }
        self.global_round = 0
    
    def broadcast_model(self) -> Dict[str, np.ndarray]:
        """Broadcast current global model to clients"""
        return self.global_model.copy()
    
    def receive_update(self, update: ModelUpdate):
        """Receive model update from a client"""
        self.client_updates.append(update)
    
    def aggregate_updates(self, method: str = "fedavg") -> Dict[str, np.ndarray]:
        """
        Aggregate client updates
        
        Args:
            method: Aggregation method ("fedavg", "fedprox", etc.)
            
        Returns:
            Updated global model weights
        """
        if not self.client_updates:
            return self.global_model
        
        if method == "fedavg":
            return self._fedavg()
        else:
            return self._fedavg()
    
    def _fedavg(self) -> Dict[str, np.ndarray]:
        """Federated Averaging (FedAvg)"""
        total_samples = sum(u.num_samples for u in self.client_updates)
        
        aggregated = {}
        for key in self.global_model.keys():
            weighted_sum = np.zeros_like(self.global_model[key])
            
            for update in self.client_updates:
                weight = update.num_samples / total_samples
                weighted_sum += update.weights_delta.get(key, np.zeros_like(self.global_model[key])) * weight
            
            aggregated[key] = self.global_model[key] + weighted_sum
        
        return aggregated
    
    def run_round(self) -> Dict:
        """Run one FL round"""
        self.global_round += 1
        
        # In production: broadcast to clients, receive updates, aggregate
        # For now: simulate aggregation
        
        aggregated_weights = self.aggregate_updates()
        self.global_model = aggregated_weights
        
        avg_accuracy = np.mean([u.metrics.get('accuracy', 0) for u in self.client_updates])
        
        round_result = {
            'round': self.global_round,
            'num_clients': len(self.client_updates),
            'avg_accuracy': avg_accuracy,
            'total_samples': sum(u.num_samples for u in self.client_updates)
        }
        
        self.history.append(round_result)
        self.client_updates = []  # Clear for next round
        
        return round_result
    
    def get_history(self) -> List[Dict]:
        """Get training history"""
        return self.history
    
    def get_model(self) -> Dict[str, np.ndarray]:
        """Get current global model"""
        return self.global_model.copy()


class FLManager:
    """Manager for FL in GreenRAN context"""
    
    def __init__(self):
        self.server = FLServer(num_clients=2)  # Slicer + Energy
        self.is_running = False
        self.round_interval = 60  # seconds
        
        # Initialize with simple model
        self.server.initialize_model({
            'layer1': (10, 10),
            'layer2': (10, 5)
        })
    
    def start_training(self):
        """Start federated learning"""
        self.is_running = True
    
    def stop_training(self):
        """Stop federated learning"""
        self.is_running = False
    
    def trigger_round(self) -> Dict:
        """Trigger a new FL round"""
        if not self.is_running:
            return {'status': 'not_running'}
        
        return self.server.run_round()
    
    def get_status(self) -> Dict:
        """Get FL status"""
        return {
            'is_running': self.is_running,
            'current_round': self.server.global_round,
            'total_rounds': len(self.server.history),
            'history': self.server.get_history()[-5:]  # Last 5 rounds
        }
    
    def save_model(self, path: str):
        """Save global model"""
        model = self.server.get_model()
        # Convert numpy arrays to lists for JSON serialization
        serializable_model = {
            k: v.tolist() for k, v in model.items()
        }
        
        with open(path, 'w') as f:
            json.dump({
                'model': serializable_model,
                'round': self.server.global_round,
                'timestamp': datetime.now().isoformat()
            }, f)
    
    def load_model(self, path: str):
        """Load global model"""
        with open(path, 'r') as f:
            data = json.load(f)
        
        self.server.global_model = {
            k: np.array(v) for k, v in data['model'].items()
        }
        self.server.global_round = data['round']


if __name__ == "__main__":
    # Test
    manager = FLManager()
    print(f"FL Manager initialized")
    print(f"Status: {manager.get_status()}")
    
    # Simulate a round
    manager.start_training()
    result = manager.trigger_round()
    print(f"Round result: {result}")
    print(f"Status after round: {manager.get_status()}")