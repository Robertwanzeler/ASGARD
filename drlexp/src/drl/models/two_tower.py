#!/usr/bin/env python3
"""
Two-Tower Encoder for Interaction Learning
========================================
Implementation based on IEEE WCNC 2025 paper:
"Conflict Detection in AI-RAN: Efficient Interaction Learning and Autonomous Graph Reconstruction"

This implements:
- Two independent encoders for Parameters and KPIs
- Scaled Cosine Similarity for learning interactions
- BCE loss for training

Author: GreenRAN Team - UFPA
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional
import numpy as np


class ParameterEncoder(nn.Module):
    """
    Encoder tower for RAN parameters.
    
    Architecture: Linear -> ReLU -> Linear
    """
    
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int = 16):
        """
        Initialize parameter encoder.
        
        Args:
            input_dim: Number of input features (L samples)
            hidden_dim: Hidden layer dimension
            output_dim: Latent space dimension (H)
        """
        super().__init__()
        
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim)
        )
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Encode parameters to latent space.
        
        Args:
            x: Input tensor (batch, num_params, L)
            
        Returns:
            Embeddings (batch, num_params, H)
        """
        batch_size = x.size(0)
        num_params = x.size(1)
        
        # Flatten for linear layer
        x_flat = x.view(batch_size * num_params, -1)
        
        # Encode
        embeddings = self.encoder(x_flat)
        
        # Reshape back
        embeddings = embeddings.view(batch_size, num_params, -1)
        
        return embeddings


class KPIEncoder(nn.Module):
    """
    Encoder tower for KPIs.
    
    Architecture: Linear -> ReLU -> Linear
    """
    
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int = 16):
        """
        Initialize KPI encoder.
        
        Args:
            input_dim: Number of input features (L samples)
            hidden_dim: Hidden layer dimension
            output_dim: Latent space dimension (H)
        """
        super().__init__()
        
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim)
        )
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Encode KPIs to latent space.
        
        Args:
            x: Input tensor (batch, num_kpis, L)
            
        Returns:
            Embeddings (batch, num_kpis, H)
        """
        batch_size = x.size(0)
        num_kpis = x.size(1)
        
        x_flat = x.view(batch_size * num_kpis, -1)
        embeddings = self.encoder(x_flat)
        embeddings = embeddings.view(batch_size, num_kpis, -1)
        
        return embeddings


class TwoTowerEncoder(nn.Module):
    """
    Two-Tower Encoder for learning interactions between parameters and KPIs.
    
    Architecture:
        - Parameter Tower: encodes Xp to Zp
        - KPI Tower: encodes Xk to Zk
        - Scaled Cosine Similarity: computes Spk = α × |Zp| × |Zk|ᵀ
    
    Based on the paper's approach for efficient interaction learning.
    """
    
    def __init__(self, 
                 param_input_dim: int = 10000,  # L samples
                 kpi_input_dim: int = 10000,
                 hidden_dim: int = 64,
                 latent_dim: int = 16):
        """
        Initialize Two-Tower Encoder.
        
        Args:
            param_input_dim: Input dimension for parameters (L)
            kpi_input_dim: Input dimension for KPIs (L)
            hidden_dim: Hidden layer dimension
            latent_dim: Latent space dimension (H)
        """
        super().__init__()
        
        self.param_encoder = ParameterEncoder(param_input_dim, hidden_dim, latent_dim)
        self.kpi_encoder = KPIEncoder(kpi_input_dim, hidden_dim, latent_dim)
        
        # Learnable scale parameter (alpha from paper)
        self.alpha = nn.Parameter(torch.ones(1) * 2.0)
        
        self.latent_dim = latent_dim
        
    def forward(self, Xp: torch.Tensor, Xk: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Forward pass through Two-Tower encoder.
        
        Args:
            Xp: Parameters (batch, num_params, L)
            Xk: KPIs (batch, num_kpis, L)
            
        Returns:
            Tuple of (Zp, Zk, Spk)
            - Zp: Parameter embeddings (batch, num_params, H)
            - Zk: KPI embeddings (batch, num_kpis, H)
            - Spk: Interaction scores (batch, num_params, num_kpis)
        """
        # Encode
        Zp = self.param_encoder(Xp)  # (batch, num_params, H)
        Zk = self.kpi_encoder(Xk)      # (batch, num_kpis, H)
        
        # L2 normalize (eq. 7 in paper)
        Zp_norm = F.normalize(Zp, p=2, dim=-1)
        Zk_norm = F.normalize(Zk, p=2, dim=-1)
        
        # Scaled cosine similarity (eq. 8 in paper)
        Spk = self.alpha * torch.bmm(Zp_norm, Zk_norm.transpose(1, 2))
        
        return Zp, Zk, Spk
    
    def get_embeddings(self, Xp: torch.Tensor, Xk: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Get latent embeddings without similarity computation.
        
        Args:
            Xp: Parameters
            Xk: KPIs
            
        Returns:
            Tuple of (Zp, Zk) embeddings
        """
        Zp = self.param_encoder(Xp)
        Zk = self.kpi_encoder(Xk)
        return Zp, Zk
    
    def compute_full_score_matrix(self, Xp: torch.Tensor, Xk: torch.Tensor) -> torch.Tensor:
        """
        Compute full score matrix S including self-interactions.
        
        This reconstructs the complete score matrix from eq. 10 in the paper.
        
        Args:
            Xp: Parameters
            Xk: KPIs
            
        Returns:
            S: Full score matrix (batch, Np+Nk, Np+Nk)
        """
        # Get embeddings
        Zp, Zk = self.get_embeddings(Xp, Xk)
        
        # Concatenate (eq. 9 in paper)
        Zall = torch.cat([Zp, Zk], dim=1)  # (batch, Np+Nk, H)
        
        # L2 normalize
        Zall_norm = F.normalize(Zall, p=2, dim=-1)
        
        # Full similarity matrix (eq. 10)
        S = self.alpha * torch.bmm(Zall_norm, Zall_norm.transpose(1, 2))
        
        return S


class TwoTowerTrainer:
    """
    Trainer for Two-Tower Encoder using BCE loss.
    
    Based on eq. 14 in the paper.
    """
    
    def __init__(self, model: TwoTowerEncoder, learning_rate: float = 0.001):
        """
        Initialize trainer.
        
        Args:
            model: Two-Tower Encoder model
            learning_rate: Learning rate
        """
        self.model = model
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        
    def compute_bce_loss(self, Spk: torch.Tensor, Y: torch.Tensor) -> torch.Tensor:
        """
        Compute Binary Cross Entropy loss.
        
        Args:
            Spk: Predicted interaction scores (batch, num_params, num_kpis)
            Y: Ground truth labels (num_params, num_kpis) or (batch, num_params, num_kpis)
            
        Returns:
            BCE loss
        """
        # Ensure Y has correct shape
        if Y.dim() == 2:
            Y = Y.unsqueeze(0).expand(Spk.size(0), -1, -1)
        
        # BCE loss (eq. 14 in paper)
        # ℓ(x, y) = max(x, 0) - xy + log(1 + exp(-|x|))
        # We use torch.nn.functional.binary_cross_entropy_with_logits
        
        loss = F.binary_cross_entropy_with_logits(Spk, Y.float())
        
        return loss
    
    def train_step(self, Xp: torch.Tensor, Xk: torch.Tensor, Y: torch.Tensor) -> dict:
        """
        Perform one training step.
        
        Args:
            Xp: Parameters batch
            Xk: KPIs batch
            Y: Ground truth labels
            
        Returns:
            Dictionary with loss and metrics
        """
        self.optimizer.zero_grad()
        
        # Forward pass
        Zp, Zk, Spk = self.model(Xp, Xk)
        
        # Compute loss
        loss = self.compute_bce_loss(Spk, Y)
        
        # Backward pass
        loss.backward()
        self.optimizer.step()
        
        # Compute predictions
        with torch.no_grad():
            predictions = (torch.sigmoid(Spk) > 0.5).float()
            accuracy = (predictions == Y.float()).float().mean()
        
        return {
            'loss': loss.item(),
            'accuracy': accuracy.item(),
            'alpha': self.model.alpha.item()
        }
    
    def evaluate(self, Xp: torch.Tensor, Xk: torch.Tensor, Y: torch.Tensor) -> dict:
        """
        Evaluate model on validation data.
        
        Args:
            Xp: Parameters
            Xk: KPIs
            Y: Ground truth
            
        Returns:
            Dictionary with metrics
        """
        self.model.eval()
        
        with torch.no_grad():
            Zp, Zk, Spk = self.model(Xp, Xk)
            loss = self.compute_bce_loss(Spk, Y)
            
            # Predictions
            predictions = (torch.sigmoid(Spk) > 0.5).float()
            
            # Metrics
            accuracy = (predictions == Y.float()).float().mean()
            
            # AUC (approximation)
            probs = torch.sigmoid(Spk)
            auc = self._compute_auc(probs, Y)
        
        self.model.train()
        
        return {
            'loss': loss.item(),
            'accuracy': accuracy.item(),
            'auc': auc
        }
    
    def _compute_auc(self, predictions: torch.Tensor, targets: torch.Tensor) -> float:
        """Compute AUC approximation."""
        # Flatten
        pred_flat = predictions.flatten().cpu().numpy()
        target_flat = targets.flatten().cpu().numpy()
        
        # Simple AUC calculation
        from sklearn.metrics import roc_auc_score
        try:
            auc = roc_auc_score(target_flat, pred_flat)
        except:
            auc = 0.5
            
        return auc


def create_two_tower_model(config: dict) -> TwoTowerEncoder:
    """
    Factory function to create Two-Tower model from config.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        TwoTowerEncoder instance
    """
    return TwoTowerEncoder(
        param_input_dim=config.get('param_input_dim', 10000),
        kpi_input_dim=config.get('kpi_input_dim', 10000),
        hidden_dim=config.get('hidden_dim', 64),
        latent_dim=config.get('latent_dim', 16)
    )


# Standalone test
if __name__ == "__main__":
    import numpy as np
    
    print("=" * 60)
    print("Two-Tower Encoder Test")
    print("=" * 60)
    
    # Create model
    model = TwoTowerEncoder(
        param_input_dim=1000,  # L samples
        kpi_input_dim=1000,
        hidden_dim=64,
        latent_dim=16
    )
    
    print(f"\nModel architecture:")
    print(model)
    
    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    print(f"\nTotal parameters: {total_params:,}")
    
    # Test forward pass
    batch_size = 4
    num_params = 7
    num_kpis = 4
    L = 1000  # samples per param/kpi
    
    Xp = torch.randn(batch_size, num_params, L)
    Xk = torch.randn(batch_size, num_kpis, L)
    
    print(f"\nInput shapes:")
    print(f"  Xp: {Xp.shape}")
    print(f"  Xk: {Xk.shape}")
    
    # Forward
    Zp, Zk, Spk = model(Xp, Xk)
    
    print(f"\nOutput shapes:")
    print(f"  Zp: {Zp.shape}")
    print(f"  Zk: {Zk.shape}")
    print(f"  Spk: {Spk.shape}")
    
    # Test full score matrix
    S = model.compute_full_score_matrix(Xp, Xk)
    print(f"  S (full): {S.shape}")
    
    # Test trainer
    print("\n" + "=" * 60)
    print("Training Test")
    print("=" * 60)
    
    trainer = TwoTowerTrainer(model, learning_rate=0.001)
    
    # Ground truth
    Y = torch.randint(0, 2, (num_params, num_kpis)).float()
    
    # Training step
    for i in range(5):
        metrics = trainer.train_step(Xp, Xk, Y)
        print(f"Step {i+1}: Loss={metrics['loss']:.4f}, Acc={metrics['accuracy']:.4f}")
    
    # Evaluate
    val_metrics = trainer.evaluate(Xp, Xk, Y)
    print(f"\nValidation: Loss={val_metrics['loss']:.4f}, Acc={val_metrics['accuracy']:.4f}, AUC={val_metrics['auc']:.4f}")
    
    print("\n✅ Two-Tower Encoder test passed!")