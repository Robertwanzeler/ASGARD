import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Tuple, Optional
import numpy as np


class ConflictDetector(nn.Module):
    """
    Conflict Detection Module (Stage 3 of WCNC 2025 Paper)
    Identifies conflicts between AI agents: Direct, Indirect, Implicit
    """
    
    def __init__(
        self,
        num_agents: int = 4,
        embedding_dim: int = 128,
        hidden_dim: int = 256,
        num_layers: int = 2,
        dropout: float = 0.3
    ):
        super().__init__()
        
        self.num_agents = num_agents
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        
        self.agent_embeddings = nn.Embedding(num_agents, embedding_dim)
        
        self.query_proj = nn.Linear(embedding_dim, embedding_dim)
        self.key_proj = nn.Linear(embedding_dim, embedding_dim)
        self.value_proj = nn.Linear(embedding_dim, embedding_dim)
        
        self.direct_conflict = nn.Sequential(
            nn.Linear(embedding_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, 1),
            nn.Sigmoid()
        )
        
        self.indirect_conflict = nn.GRU(
            embedding_dim,
            hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0
        )
        
        self.indirect_classifier = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, num_agents)
        )
        
        self.implicit_encoder = nn.Sequential(
            nn.Linear(embedding_dim * num_agents, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_agents * num_agents)
        )
        
        self.confidence_weight = nn.Parameter(torch.tensor(0.5))
        
        self._init_weights()
    
    def _init_weights(self):
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
    
    def encode_agents(self, agent_ids: torch.Tensor) -> torch.Tensor:
        return self.agent_embeddings(agent_ids)
    
    def compute_direct_conflict(
        self,
        agent_emb: torch.Tensor,
        other_agent_emb: torch.Tensor
    ) -> torch.Tensor:
        combined = torch.cat([agent_emb, other_agent_emb], dim=-1)
        return self.direct_conflict(combined)
    
    def compute_indirect_conflict(
        self,
        agent_emb: torch.Tensor,
        context_emb: torch.Tensor
    ) -> torch.Tensor:
        seq = torch.stack([agent_emb, context_emb], dim=1)
        output, hidden = self.indirect_conflict(seq)
        logits = self.indirect_classifier(hidden[-1])
        return F.softmax(logits, dim=-1)
    
    def compute_implicit_conflict(
        self,
        all_embeddings: torch.Tensor
    ) -> torch.Tensor:
        flat = all_embeddings.view(-1, self.embedding_dim * self.num_agents)
        logits = self.implicit_encoder(flat)
        return logits.view(-1, self.num_agents, self.num_agents)
    
    def forward(
        self,
        agent_ids: torch.Tensor,
        focus_agent: int = 0,
        return_all: bool = False
    ) -> Dict[str, torch.Tensor]:
        batch_size = agent_ids.size(0)
        
        agent_emb = self.encode_agents(agent_ids)
        
        if isinstance(focus_agent, torch.Tensor):
            focus_agent = focus_agent.item()
        
        focus_emb = agent_emb[:, focus_agent]
        other_emb = torch.cat([
            agent_emb[:, :focus_agent],
            agent_emb[:, focus_agent + 1:]
        ], dim=1)
        
        all_directConflicts = []
        for i in range(self.num_agents - 1):
            direct_conf = self.compute_direct_conflict(
                focus_emb,
                other_emb[:, i]
            )
            all_directConflicts.append(direct_conf)
        
        direct_conflicts = torch.cat(all_directConflicts, dim=-1)
        direct_conflicts = direct_conflicts.mean(dim=-1, keepdim=True)
        
        context_emb = other_emb.mean(dim=1)
        indirect_conflicts = self.compute_indirect_conflict(focus_emb, context_emb)
        
        implicit_conflicts = self.compute_implicit_conflict(agent_emb)
        
        results = {
            'direct_conflict': direct_conflicts,
            'indirect_conflict': indirect_conflicts,
            'implicit_conflict': implicit_conflicts,
            'agent_embeddings': agent_emb
        }
        
        if return_all:
            results['focus_embedding'] = focus_emb
        
        return results
    
    def detect_conflicts(
        self,
        agent_ids: torch.Tensor,
        threshold: float = 0.5,
        device: str = 'cpu'
    ) -> Dict[str, np.ndarray]:
        self.eval()
        
        with torch.no_grad():
            forward_results = self.forward(agent_ids.to(device))
        
        direct_probs = forward_results['direct_conflict'].cpu().numpy()
        indirect_probs = forward_results['indirect_conflict'].cpu().numpy()
        implicit_matrix = forward_results['implicit_conflict'].cpu().numpy()
        
        conflicts = {
            'direct': (direct_probs > threshold).astype(int),
            'indirect': (indirect_probs > threshold).astype(int),
            'implicit': (implicit_matrix > threshold).astype(int),
            'direct_probs': direct_probs,
            'indirect_probs': indirect_probs,
            'implicit_probs': implicit_matrix,
            'combined_score': (
                self.confidence_weight.item() * direct_probs +
                (1 - self.confidence_weight.item()) * indirect_probs.mean(axis=-1, keepdims=True)
            )
        }
        
        return conflicts


class ConflictDetectorWithTwoTower(nn.Module):
    """
    Combines Two-Tower Encoder with Conflict Detection
    For end-to-end conflict resolution pipeline
    """
    
    def __init__(
        self,
        two_tower_model: nn.Module,
        conflict_detector: ConflictDetector,
        num_agents: int = 4,
        num_kpis: int = 4,
        embedding_dim: int = 128
    ):
        super().__init__()
        
        self.two_tower = two_tower_model
        self.conflict_detector = conflict_detector
        
        self.cross_attention = nn.MultiheadAttention(
            embed_dim=embedding_dim,
            num_heads=4,
            dropout=0.1,
            batch_first=True
        )
        
        self.fusion = nn.Sequential(
            nn.Linear(embedding_dim * 2, embedding_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(embedding_dim, embedding_dim // 2),
            nn.ReLU(),
            nn.Linear(embedding_dim // 2, 1)
        )
        
        self.decision_head = nn.Sequential(
            nn.Linear(num_kpis, num_kpis * 2),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(num_kpis * 2, num_kpis),
            nn.Sigmoid()
        )
    
    def forward(
        self,
        encoder_ids: torch.Tensor,
        agent_ids: torch.Tensor,
        kpis: torch.Tensor
    ) -> Tuple[Dict[str, torch.Tensor], torch.Tensor]:
        tower_output = self.two_tower(encoder_ids, agent_ids)
        conflict_output = self.conflict_detector(agent_ids)
        
        encoder_emb = tower_output['agent_embeddings']
        conflict_emb = conflict_output['agent_embeddings']
        
        fused_emb = torch.cat([encoder_emb, conflict_emb], dim=-1)
        
        attn_output, _ = self.cross_attention(
            fused_emb.unsqueeze(1),
            fused_emb.unsqueeze(1),
            fused_emb.unsqueeze(1)
        )
        
        fused = attn_output.squeeze(1)
        decision_logits = self.fusion(fused)
        
        kpi_weights = self.decision_head(kpis)
        
        return {
            'conflict_detector': conflict_output,
            'two_tower': tower_output,
            'fused_embedding': fused,
            'kpi_weights': kpi_weights
        }, decision_logits
    
    def resolve_conflict(
        self,
        encoder_ids: torch.Tensor,
        agent_ids: torch.Tensor,
        kpis: torch.Tensor,
        device: str = 'cpu'
    ) -> Dict[str, np.ndarray]:
        self.eval()
        
        with torch.no_grad():
            outputs, decisions = self.forward(
                encoder_ids.to(device),
                agent_ids.to(device),
                kpis.to(device)
            )
        
        conflict_results = self.conflict_detector.detect_conflicts(
            agent_ids.to(device),
            device=device
        )
        
        return {
            'conflict_types': conflict_results,
            'decisions': decisions.cpu().numpy(),
            'kpi_weights': outputs['kpi_weights'].cpu().numpy()
        }


def create_conflict_detector(config: Dict) -> ConflictDetector:
    """Factory function to create ConflictDetector from config"""
    
    return ConflictDetector(
        num_agents=config.get('num_agents', 4),
        embedding_dim=config.get('embedding_dim', 128),
        hidden_dim=config.get('hidden_dim', 256),
        num_layers=config.get('num_layers', 2),
        dropout=config.get('dropout', 0.3)
    )


def load_checkpoint(
    model: ConflictDetector,
    checkpoint_path: str,
    device: str = 'cpu'
) -> ConflictDetector:
    """Load trained model from checkpoint"""
    
    state_dict = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(state_dict)
    return model


def save_checkpoint(
    model: ConflictDetector,
    checkpoint_path: str
) -> None:
    """Save model checkpoint"""
    
    torch.save(model.state_dict(), checkpoint_path)