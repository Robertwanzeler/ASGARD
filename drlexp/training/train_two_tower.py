import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
from tqdm import tqdm
import yaml
import os
import json
from datetime import datetime


class TwoTowerTrainer:
    """
    Trainer for Two-Tower Encoder + Conflict Detection pipeline
    """
    
    def __init__(
        self,
        model: nn.Module,
        config: dict,
        device: str = 'cpu'
    ):
        self.model = model
        self.config = config
        self.device = device
        
        self.optimizer = torch.optim.Adam(
            model.parameters(),
            lr=config.get('learning_rate', 1e-4),
            weight_decay=config.get('weight_decay', 1e-5)
        )
        
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPatience(
            self.optimizer,
            mode='min',
            patience=config.get('scheduler_patience', 5),
            factor=config.get('scheduler_factor', 0.5)
        )
        
        self.scaler = torch.cuda.amp.GradScaler() if device == 'cuda' else None
    
    def compute_losses(
        self,
        outputs: dict,
        targets: dict
    ) -> dict:
        reconstruction_loss = F.mse_loss(
            outputs['reconstructed'],
            targets['features']
        )
        
        graph_loss = F.binary_cross_entropy_with_logits(
            outputs['adjacency_logits'],
            targets['adjacency'],
            reduction='mean'
        )
        
        contrastive_loss = 0.0
        if 'embeddings' in outputs and 'embeddings' in outputs:
            emb1 = outputs['embeddings']['tower1']
            emb2 = outputs['embeddings']['tower2']
            
            emb1 = F.normalize(emb1, dim=-1)
            emb2 = F.normalize(emb2, dim=-1)
            
            similarity = torch.matmul(emb1, emb2.T)
            labels = torch.arange(emb1.size(0), device=emb1.device)
            
            contrastive_loss = F.cross_entropy(similarity, labels)
        
        total_loss = (
            self.config.get('w_reconstruction', 0.5) * reconstruction_loss +
            self.config.get('w_graph', 0.3) * graph_loss +
            self.config.get('w_contrastive', 0.2) * contrastive_loss
        )
        
        return {
            'total_loss': total_loss,
            'reconstruction_loss': reconstruction_loss,
            'graph_loss': graph_loss,
            'contrastive_loss': contrastive_loss
        }
    
    def train_epoch(self, dataloader: DataLoader) -> dict:
        self.model.train()
        
        losses = {
            'total': [],
            'reconstruction': [],
            'graph': [],
            'contrastive': []
        }
        
        for batch in tqdm(dataloader, desc='Training'):
            encoder_ids = batch[0].to(self.device)
            agent_ids = batch[1].to(self.device)
            features = batch[2].to(self.device)
            adjacency = batch[3].to(self.device)
            
            self.optimizer.zero_grad()
            
            if self.scaler is not None:
                with torch.cuda.amp.autocast():
                    outputs = self.model(encoder_ids, agent_ids)
                    targets = {
                        'features': features,
                        'adjacency': adjacency
                    }
                    batch_losses = self.compute_losses(outputs, targets)
                
                self.scaler.scale(batch_losses['total_loss']).backward()
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                outputs = self.model(encoder_ids, agent_ids)
                targets = {
                    'features': features,
                    'adjacency': adjacency
                }
                batch_losses = self.compute_losses(outputs, targets)
                batch_losses['total_loss'].backward()
                self.optimizer.step()
            
            losses['total'].append(batch_losses['total_loss'].item())
            losses['reconstruction'].append(batch_losses['reconstruction_loss'].item())
            losses['graph'].append(batch_losses['graph_loss'].item())
            losses['contrastive'].append(batch_losses['contrastive_loss'].item())
        
        return {k: np.mean(v) for k, v in losses.items()}
    
    @torch.no_grad()
    def evaluate(self, dataloader: DataLoader) -> dict:
        self.model.eval()
        
        losses = {
            'total': [],
            'reconstruction': [],
            'graph': [],
            'contrastive': []
        }
        
        reconstruction_errors = []
        
        for batch in dataloader:
            encoder_ids = batch[0].to(self.device)
            agent_ids = batch[1].to(self.device)
            features = batch[2].to(self.device)
            adjacency = batch[3].to(self.device)
            
            outputs = self.model(encoder_ids, agent_ids)
            targets = {
                'features': features,
                'adjacency': adjacency
            }
            batch_losses = self.compute_losses(outputs, targets)
            
            losses['total'].append(batch_losses['total_loss'].item())
            losses['reconstruction'].append(batch_losses['reconstruction_loss'].item())
            losses['graph'].append(batch_losses['graph_loss'].item())
            losses['contrastive'].append(batch_losses['contrastive_loss'].item())
            
            recon_error = F.mse_loss(outputs['reconstructed'], features)
            reconstruction_errors.append(recon_error.item())
        
        results = {k: np.mean(v) for k, v in losses.items()}
        results['reconstruction_error'] = np.mean(reconstruction_errors)
        
        return results
    
    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        output_dir: str,
        num_epochs: int = 100
    ):
        os.makedirs(output_dir, exist_ok=True)
        
        best_val_loss = float('inf')
        patience_counter = 0
        max_patience = self.config.get('patience', 15)
        
        history = {
            'train': [],
            'val': []
        }
        
        print(f"Starting training for {num_epochs} epochs...")
        print(f"Device: {self.device}")
        print(f"Total parameters: {sum(p.numel() for p in self.model.parameters())}")
        
        for epoch in range(num_epochs):
            train_losses = self.train_epoch(train_loader)
            val_losses = self.evaluate(val_loader)
            
            self.scheduler.step(val_losses['total'])
            
            history['train'].append(train_losses)
            history['val'].append(val_losses)
            
            print(f"Epoch {epoch+1}/{num_epochs}")
            print(f"  Train - Total: {train_losses['total']:.4f}, "
                  f"Recon: {train_losses['reconstruction']:.4f}, "
                  f"Graph: {train_losses['graph']:.4f}")
            print(f"  Val   - Total: {val_losses['total']:.4f}, "
                  f"Recon Error: {val_losses['reconstruction_error']:.4f}")
            
            if val_losses['total'] < best_val_loss:
                best_val_loss = val_losses['total']
                patience_counter = 0
                
                checkpoint_path = os.path.join(output_dir, 'best_model.pt')
                torch.save({
                    'epoch': epoch,
                    'model_state_dict': self.model.state_dict(),
                    'optimizer_state_dict': self.optimizer.state_dict(),
                    'val_loss': val_losses['total'],
                    'config': self.config
                }, checkpoint_path)
                print(f"  Saved best model to {checkpoint_path}")
            else:
                patience_counter += 1
                
                if patience_counter >= max_patience:
                    print(f"Early stopping at epoch {epoch+1}")
                    break
        
        with open(os.path.join(output_dir, 'history.json'), 'w') as f:
            json.dump(history, f, indent=2)
        
        print(f"Training completed. Best validation loss: {best_val_loss:.4f}")
        
        return history


def generate_two_tower_data(
    num_samples: int,
    num_encoders: int = 7,
    num_agents: int = 4,
    feature_dim: int = 64
) -> Tuple[TensorDataset, TensorDataset]:
    """
    Generate synthetic data for Two-Tower training
    """
    
    encoder_ids = torch.randint(0, num_encoders, (num_samples, num_agents))
    agent_ids = torch.randint(0, num_agents, (num_samples, num_agents))
    
    features = torch.randn(num_samples, feature_dim)
    
    adjacency = torch.rand(num_samples, num_agents, num_agents)
    adjacency = (adjacency + adjacency.transpose(1, 2)) / 2
    adjacency = (adjacency > 0.5).float()
    
    train_size = int(num_samples * 0.8)
    
    train_dataset = TensorDataset(
        encoder_ids[:train_size],
        agent_ids[:train_size],
        features[:train_size],
        adjacency[:train_size]
    )
    
    val_dataset = TensorDataset(
        encoder_ids[train_size:],
        agent_ids[train_size:],
        features[train_size:],
        adjacency[train_size:]
    )
    
    return train_dataset, val_dataset


def main(config_path: str = 'config/drl_config.yaml'):
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    output_dir = 'checkpoints/two_tower'
    os.makedirs(output_dir, exist_ok=True)
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    
    from src.drl.two_tower import TwoTowerEncoder
    from src.drl.conflict_detector import ConflictDetector
    
    two_tower = TwoTowerEncoder(
        num_encoders=config.get('num_encoders', 7),
        num_agents=config.get('num_agents', 4),
        feature_dim=config.get('feature_dim', 64),
        embedding_dim=config.get('embedding_dim', 128),
        hidden_dim=config.get('hidden_dim', 256),
        num_layers=config.get('num_layers', 2),
        dropout=config.get('dropout', 0.3)
    )
    
    conflict_detector = ConflictDetector(
        num_agents=config.get('num_agents', 4),
        embedding_dim=config.get('embedding_dim', 128),
        hidden_dim=config.get('hidden_dim', 256),
        num_layers=config.get('num_layers', 2),
        dropout=config.get('dropout', 0.3)
    )
    
    from src.drl.conflict_detector import ConflictDetectorWithTwoTower
    
    combined_model = ConflictDetectorWithTwoTower(
        two_tower_model=two_tower,
        conflict_detector=conflict_detector,
        num_agents=config.get('num_agents', 4),
        num_kpis=config.get('num_kpis', 4),
        embedding_dim=config.get('embedding_dim', 128)
    ).to(device)
    
    train_dataset, val_dataset = generate_two_tower_data(
        num_samples=config.get('train_samples', 5000),
        num_encoders=config.get('num_encoders', 7),
        num_agents=config.get('num_agents', 4),
        feature_dim=config.get('feature_dim', 64)
    )
    
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.get('batch_size', 64),
        shuffle=True,
        num_workers=2
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=config.get('batch_size', 64),
        shuffle=False,
        num_workers=2
    )
    
    trainer = TwoTowerTrainer(
        model=combined_model,
        config=config,
        device=device
    )
    
    history = trainer.train(
        train_loader,
        val_loader,
        output_dir,
        num_epochs=config.get('epochs', 100)
    )
    
    return combined_model, history


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default='config/drl_config.yaml')
    args = parser.parse_args()
    
    main(args.config)