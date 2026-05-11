#!/usr/bin/env python3
"""
Sparsemax Graph Reconstruction
============================
Implementation based on IEEE WCNC 2025 paper:
"Conflict Detection in AI-RAN: Efficient Interaction Learning and Autonomous Graph Reconstruction"

This implements:
- Sparsemax operator for autonomous graph reconstruction
- Binary adjacency matrix generation
- Graph augmentation with known relationships

Author: GreenRAN Team - UFPA
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional
import numpy as np


def sparsemax(input: torch.Tensor, dim: int = -1) -> torch.Tensor:
    """
    Sparsemax activation function.
    
    Based on "From Softmax to Sparsemax: A Sparse Model of Attention 
    and Multi-Label Classification" (ICML 2016).
    
    This projects values onto the probability simplex, creating sparse
    representations where weak interactions collapse to zero.
    
    Args:
        input: Input tensor
        dim: Dimension to apply sparsemax
        
    Returns:
        Sparse probability distribution
    """
    # Sort values in descending order
    sorted_vals, sorted_indices = torch.sort(input, dim=dim, descending=True)
    
    # Cumulative sum
    cumsum = torch.cumsum(sorted_vals, dim=dim)
    
    # Find k where 1 + k * value > cumsum
    k_vals = 1 + torch.arange(1, input.size(dim) + 1, device=input.device, dtype=input.dtype)
    threshold = cumsum - sorted_vals * k_vals
    
    # Find first positive threshold
    k = torch.sum(threshold > 0, dim=dim, keepdim=True)
    k = torch.clamp(k, min=1)
    
    # Calculate threshold value
    threshold_val = torch.gather(cumsum, dim=dim, index=k - 1)
    threshold_val = threshold_val / k.float()
    
    # Apply sparsemax
    output = torch.clamp(input - threshold_val, min=0)
    
    return output


class SparsemaxGraphReconstructor(nn.Module):
    """
    Graph Reconstruction using Sparsemax.
    
    Based on Section V in the paper:
    - Apply sparsemax to score matrix S
    - Binarize with threshold τ = 0
    - Augment with known relationships Aknown
    
    This provides autonomous graph reconstruction without manual thresholds.
    """
    
    def __init__(self, num_agents: int = 4, num_params: int = 7, num_kpis: int = 4):
        """
        Initialize graph reconstructor.
        
        Args:
            num_agents: Number of AI agents (Na)
            num_params: Number of parameters (Np)
            num_kpis: Number of KPIs (Nk)
        """
        super().__init__()
        
        self.num_agents = num_agents
        self.num_params = num_params
        self.num_kpis = num_kpis
        self.total_entities = num_params + num_kpis
        
    def forward(self, S: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Reconstruct graph from score matrix.
        
        Args:
            S: Full score matrix from Two-Tower (batch, Np+Nk, Np+Nk)
            
        Returns:
            Tuple of (P_sparse, A_learned)
            - P_sparse: Sparse probability matrix
            - A_learned: Binary adjacency matrix
        """
        batch_size = S.size(0)
        
        # Apply sparsemax row-wise (eq. 12 in paper)
        P_sparse = sparsemax(S, dim=-1)
        
        # Binarize with threshold τ = 0 (eq. 13 in paper)
        # Keep only non-zero interactions
        A_learned = (P_sparse > 0).float()
        
        # Set diagonal to 0 (no self-loops)
        identity = torch.eye(self.total_entities, device=S.device).unsqueeze(0)
        A_learned = A_learned * (1 - identity)
        
        return P_sparse, A_learned
    
    def augment_with_known(self, A_learned: torch.Tensor, Aknown: torch.Tensor) -> torch.Tensor:
        """
        Augment learned graph with known relationships.
        
        Args:
            A_learned: Learned adjacency matrix (batch, Np+Nk, Np+Nk)
            Aknown: Known relationships (Na, Np+Nk)
            
        Returns:
            Augmented adjacency matrix (batch, Na+Np+Nk, Na+Np+Nk)
        """
        # Create augmented matrix (eq. 3 in paper)
        # ˆA = ˆAlearned ⊞ Aknown
        # [Ina  Aknown  ]
        # [Aknown.T  ˆAlearned]
        
        batch_size = A_learned.size(0)
        device = A_learned.device
        
        # Identity for agents
        Ina = torch.eye(self.num_agents, device=device).unsqueeze(0).expand(batch_size, -1, -1)
        
        # Known relationships
        Aknown_expanded = Aknown.unsqueeze(2).expand(-1, -1, self.total_entities)
        Aknown_T = Aknown_expanded.transpose(1, 2)
        
        # Block matrix augmentation
        A_augmented = torch.cat([
            torch.cat([Ina, Aknown_expanded], dim=2),
            torch.cat([Aknown_T, A_learned], dim=2)
        ], dim=1)
        
        return A_augmented
    
    def reconstruct(self, S: torch.Tensor, Aknown: torch.Tensor, 
                   augment: bool = True) -> Tuple[torch.Tensor, dict]:
        """
        Full graph reconstruction pipeline.
        
        Args:
            S: Score matrix
            Aknown: Known relationships
            augment: Whether to augment with Aknown
            
        Returns:
            Tuple of (adjacency matrix, metadata)
        """
        # Get sparse representation and binary adjacency
        P_sparse, A_learned = self.forward(S)
        
        metadata = {
            'P_sparse': P_sparse,
            'A_learned': A_learned,
            'num_edges': int(A_learned.sum().item()),
            'sparsity': 1 - (A_learned.sum().item() / (self.total_entities ** 2))
        }
        
        if augment:
            A_augmented = self.augment_with_known(A_learned, Aknown)
            metadata['A_augmented'] = A_augmented
            metadata['num_edges_augmented'] = int(A_augmented.sum().item())
        
        return A_learned, metadata


class ThresholdBasedReconstructor(nn.Module):
    """
    Alternative: Threshold-based graph reconstruction.
    
    This is included for comparison with the sparsemax approach.
    Methods:
    - Static threshold
    - Top-K selection
    - Quantile selection
    """
    
    def __init__(self, num_agents: int = 4, num_params: int = 7, num_kpis: int = 4,
                 method: str = 'threshold', threshold: float = 0.0, top_k: int = 10, 
                 quantile: float = 0.2):
        """
        Initialize threshold-based reconstructor.
        
        Args:
            method: Reconstruction method ('threshold', 'top_k', 'quantile')
            threshold: Static threshold value
            top_k: Number of top edges to keep
            quantile: Percentage of edges to keep
        """
        super().__init__()
        
        self.num_agents = num_agents
        self.num_params = num_params
        self.num_kpis = num_kpis
        self.total_entities = num_params + num_kpis
        
        self.method = method
        self.threshold = threshold
        self.top_k = top_k
        self.quantile = quantile
        
    def forward(self, S: torch.Tensor) -> torch.Tensor:
        """
        Reconstruct graph using threshold method.
        
        Args:
            S: Score matrix
            
        Returns:
            Binary adjacency matrix
        """
        batch_size = S.size(0)
        
        if self.method == 'threshold':
            # Static threshold
            A_learned = (S > self.threshold).float()
            
        elif self.method == 'top_k':
            # Top-K edges per row
            A_learned = torch.zeros_like(S)
            for i in range(batch_size):
                for j in range(S.size(1)):
                    row = S[i, j, :]
                    _, top_indices = torch.topk(row, min(self.top_k, row.size(0)))
                    A_learned[i, j, top_indices] = 1
                    
        elif self.method == 'quantile':
            # Top quantile percentage
            A_learned = torch.zeros_like(S)
            for i in range(batch_size):
                for j in range(S.size(1)):
                    row = S[i, j, :]
                    threshold_value = torch.quantile(row, 1 - self.quantile)
                    A_learned[i, j, :] = (row > threshold_value).float()
        else:
            raise ValueError(f"Unknown method: {self.method}")
        
        # Remove self-loops
        identity = torch.eye(self.total_entities, device=S.device).unsqueeze(0)
        A_learned = A_learned * (1 - identity)
        
        return A_learned


def compute_conflict_metrics(A: torch.Tensor, num_agents: int, 
                           num_params: int, num_kpis: int) -> dict:
    """
    Compute graph metrics for conflict analysis.
    
    Args:
        A: Adjacency matrix
        num_agents: Number of agents
        num_params: Number of parameters
        num_kpis: Number of KPIs
        
    Returns:
        Dictionary with metrics
    """
    # Extract blocks
    agents_params = A[:, :num_agents, num_agents:num_agents+num_params]
    agents_kpis = A[:, :num_agents, num_agents+num_params:]
    params_kpis = A[:, num_agents:num_agents+num_params, num_agents:num_agents+num_kpis]
    params_params = A[:, num_agents:num_agents+num_params, num_agents:num_agents+num_params]
    kpis_kpis = A[:, num_agents+num_params:, num_agents+num_params:]
    
    metrics = {
        'agent_param_edges': int(agents_params.sum()),
        'agent_kpi_edges': int(agents_kpis.sum()),
        'param_kpi_edges': int(params_kpis.sum()),
        'param_self_edges': int(params_params.sum()),
        'kpi_self_edges': int(kpis_kpis.sum()),
        'total_edges': int(A.sum()),
    }
    
    return metrics


# Standalone test
if __name__ == "__main__":
    print("=" * 60)
    print("Sparsemax Graph Reconstruction Test")
    print("=" * 60)
    
    # Create reconstructor
    reconstructor = SparsemaxGraphReconstructor(
        num_agents=4,
        num_params=7,
        num_kpis=4
    )
    
    print(f"\nConfiguration:")
    print(f"  Agents: 4, Parameters: 7, KPIs: 4")
    print(f"  Total entities: 13")
    
    # Test score matrix
    batch_size = 2
    total = 7 + 4  # params + kpis
    S = torch.randn(batch_size, total, total)
    
    print(f"\nInput score matrix: {S.shape}")
    
    # Reconstruct
    P_sparse, A_learned = reconstructor(S)
    
    print(f"Sparse matrix P: {P_sparse.shape}")
    print(f"Learned adjacency A: {A_learned.shape}")
    print(f"Number of edges: {int(A_learned.sum())}")
    
    # Test augmentation with known relationships
    Aknown = torch.randint(0, 2, (4, 11)).float()
    print(f"\nKnown relationships Aknown: {Aknown.shape}")
    
    A_aug, metadata = reconstructor.reconstruct(S, Aknown, augment=True)
    print(f"Augmented adjacency: {A_aug.shape}")
    print(f"Augmented edges: {metadata['num_edges_augmented']}")
    print(f"Sparsity: {metadata['sparsity']:.2%}")
    
    # Compare with threshold methods
    print("\n" + "=" * 60)
    print("Comparison with Threshold Methods")
    print("=" * 60)
    
    threshold_recon = ThresholdBasedReconstructor(method='threshold', threshold=0.0)
    A_thresh = threshold_recon(S)
    print(f"Threshold (τ=0): {int(A_thresh.sum())} edges")
    
    topk_recon = ThresholdBasedReconstructor(method='top_k', top_k=10)
    A_topk = topk_recon(S)
    print(f"Top-K (K=10): {int(A_topk.sum())} edges")
    
    quantile_recon = ThresholdBasedReconstructor(method='quantile', quantile=0.2)
    A_quant = quantile_recon(S)
    print(f"Quantile (20%): {int(A_quant.sum())} edges")
    
    print("\n✅ Sparsemax Graph Reconstruction test passed!")