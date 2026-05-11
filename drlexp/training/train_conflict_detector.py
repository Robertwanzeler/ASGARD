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


class ConflictLoss(nn.Module):
    """
    Multi-task loss for conflict detection:
    - Direct conflict: Binary cross-entropy
    - Indirect conflict: Cross-entropy
    - Implicit conflict: Matrix cross-entropy
    """
    
    def __init__(
        self,
        w_direct: float = 0.4,
        w_indirect: float = 0.3,
        w_implicit: float = 0.3
    ):
        super().__init__()
        self.w_direct = w_direct
        self.w_indirect = w_indirect
        self.w_implicit = w_implicit
    
    def forward(
        self,
        predictions: Dict[str, torch.Tensor],
        targets: Dict[str, torch.Tensor]
    ) -> Dict[str, torch.Tensor]:
        direct_loss = F.binary_cross_entropy(
            predictions['direct'],
            targets['direct']
        )
        
        indirect_loss = F.cross_entropy(
            predictions['indirect'],
            targets['indirect'],
            reduction='mean'
        )
        
        implicit_targets = targets['implicit'].view(-1, targets['implicit'].size(-1))
        implicit_preds = predictions['implicit'].view(-1, predictions['implicit'].size(-1))
        implicit_loss = F.cross_entropy(
            implicit_preds,
            implicit_targets,
            reduction='mean'
        )
        
        total_loss = (
            self.w_direct * direct_loss +
            self.w_indirect * indirect_loss +
            self.w_implicit * implicit_loss
        )
        
        return {
            'total_loss': total_loss,
            'direct_loss': direct_loss,
            'indirect_loss': indirect_loss,
            'implicit_loss': implicit_loss
        }


def generate_synthetic_conflicts(
    num_samples: int,
    num_agents: int = 4,
    conflict_rate: float = 0.3
) -> Tuple[Dict[str, torch.Tensor], Dict[str, torch.Tensor]]:
    """
    Generate synthetic conflict data for training
    """
    
    agent_ids = torch.randint(0, num_agents, (num_samples, num_agents))
    
    direct_targets = torch.rand(num_samples, 1)
    direct_targets = (direct_targets < conflict_rate).float()
    
    indirect_targets = torch.randint(0, num_agents, (num_samples,))
    
    implicit_targets = torch.rand(num_samples, num_agents, num_agents)
    implicit_targets = (implicit_targets < conflict_rate).float()
    
    inputs = {
        'agent_ids': agent_ids,
        'focus_agent': torch.zeros(num_samples, dtype=torch.long)
    }
    
    targets = {
        'direct': direct_targets,
        'indirect': indirect_targets,
        'implicit': implicit_targets
    }
    
    return inputs, targets


def train_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: ConflictLoss,
    device: str = 'cpu',
    scaler: torch.cuda.amp.GradScaler = None
) -> Dict[str, float]:
    model.train()
    
    total_losses = []
    direct_losses = []
    indirect_losses = []
    implicit_losses = []
    
    for batch in dataloader:
        agent_ids = batch[0].to(device)
        focus_agent = batch[1].to(device)
        
        direct_target = batch[2].to(device)
        indirect_target = batch[3].to(device)
        implicit_target = batch[4].to(device)
        
        optimizer.zero_grad()
        
        if scaler is not None:
            with torch.cuda.amp.autocast():
                outputs = model(agent_ids, focus_agent.item())
                
                predictions = {
                    'direct': outputs['direct_conflict'],
                    'indirect': outputs['indirect_conflict'].squeeze(1),
                    'implicit': outputs['implicit_conflict'].view(-1, model.num_agents * model.num_agents)
                }
                
                targets = {
                    'direct': direct_target,
                    'indirect': indirect_target,
                    'implicit': implicit_target.view(-1)
                }
                
                losses = criterion(predictions, targets)
            
            scaler.scale(losses['total_loss']).backward()
            scaler.step(optimizer)
            scaler.update()
        else:
            outputs = model(agent_ids, focus_agent.item())
            
            predictions = {
                'direct': outputs['direct_conflict'],
                'indirect': outputs['indirect_conflict'].squeeze(1),
                'implicit': outputs['implicit_conflict'].view(-1, model.num_agents * model.num_agents)
            }
            
            targets = {
                'direct': direct_target,
                'indirect': indirect_target,
                'implicit': implicit_target.view(-1)
            }
            
            losses = criterion(predictions, targets)
            losses['total_loss'].backward()
            optimizer.step()
        
        total_losses.append(losses['total_loss'].item())
        direct_losses.append(losses['direct_loss'].item())
        indirect_losses.append(losses['indirect_loss'].item())
        implicit_losses.append(losses['implicit_loss'].item())
    
    return {
        'total_loss': np.mean(total_losses),
        'direct_loss': np.mean(direct_losses),
        'indirect_loss': np.mean(indirect_losses),
        'implicit_loss': np.mean(implicit_losses)
    }


@torch.no_grad()
def evaluate(
    model: nn.Module,
    dataloader: DataLoader,
    criterion: ConflictLoss,
    device: str = 'cpu'
) -> Dict[str, float]:
    model.eval()
    
    total_losses = []
    direct_losses = []
    indirect_losses = []
    implicit_losses = []
    
    direct_correct = 0
    indirect_correct = 0
    implicit_correct = 0
    total_samples = 0
    
    for batch in dataloader:
        agent_ids = batch[0].to(device)
        focus_agent = batch[1].to(device)
        
        direct_target = batch[2].to(device)
        indirect_target = batch[3].to(device)
        implicit_target = batch[4].to(device)
        
        outputs = model(agent_ids, focus_agent.item())
        
        predictions = {
            'direct': outputs['direct_conflict'],
            'indirect': outputs['indirect_conflict'].squeeze(1),
            'implicit': outputs['implicit_conflict'].view(-1, model.num_agents * model.num_agents)
        }
        
        targets = {
            'direct': direct_target,
            'indirect': indirect_target,
            'implicit': implicit_target.view(-1)
        }
        
        losses = criterion(predictions, targets)
        
        total_losses.append(losses['total_loss'].item())
        direct_losses.append(losses['direct_loss'].item())
        indirect_losses.append(losses['indirect_loss'].item())
        implicit_losses.append(losses['implicit_loss'].item())
        
        direct_preds = (outputs['direct_conflict'] > 0.5).float()
        direct_correct += (direct_preds == direct_target).sum().item()
        
        indirect_preds = outputs['indirect_conflict'].argmax(dim=-1)
        indirect_correct += (indirect_preds == indirect_target).sum().item()
        
        implicit_preds = (outputs['implicit_conflict'] > 0.5).float()
        implicit_correct += (implicit_preds == implicit_target).sum().item()
        
        total_samples += direct_target.size(0)
    
    return {
        'total_loss': np.mean(total_losses),
        'direct_loss': np.mean(direct_losses),
        'indirect_loss': np.mean(indirect_losses),
        'implicit_loss': np.mean(implicit_losses),
        'direct_accuracy': direct_correct / total_samples,
        'indirect_accuracy': indirect_correct / total_samples,
        'implicit_accuracy': implicit_correct / total_samples
    }


def train(
    config_path: str = 'config/drl_config.yaml',
    output_dir: str = 'checkpoints/conflict_detector'
):
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    os.makedirs(output_dir, exist_ok=True)
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Using device: {device}")
    
    from src.drl.conflict_detector import ConflictDetector
    
    model = ConflictDetector(
        num_agents=config.get('num_agents', 4),
        embedding_dim=config.get('embedding_dim', 128),
        hidden_dim=config.get('hidden_dim', 256),
        num_layers=config.get('num_layers', 2),
        dropout=config.get('dropout', 0.3)
    ).to(device)
    
    train_inputs, train_targets = generate_synthetic_conflicts(
        num_samples=config.get('train_samples', 10000),
        num_agents=config.get('num_agents', 4),
        conflict_rate=config.get('conflict_rate', 0.3)
    )
    
    val_inputs, val_targets = generate_synthetic_conflicts(
        num_samples=config.get('val_samples', 2000),
        num_agents=config.get('num_agents', 4),
        conflict_rate=config.get('conflict_rate', 0.3)
    )
    
    train_dataset = TensorDataset(
        train_inputs['agent_ids'],
        train_inputs['focus_agent'],
        train_targets['direct'],
        train_targets['indirect'],
        train_targets['implicit']
    )
    
    val_dataset = TensorDataset(
        val_inputs['agent_ids'],
        val_inputs['focus_agent'],
        val_targets['direct'],
        val_targets['indirect'],
        val_targets['implicit']
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
    
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=config.get('learning_rate', 1e-4),
        weight_decay=config.get('weight_decay', 1e-5)
    )
    
    scheduler = torch.optim.lr_scheduler.ReduceLROnPatience(
        optimizer,
        mode='min',
        patience=config.get('scheduler_patience', 5),
        factor=config.get('scheduler_factor', 0.5)
    )
    
    criterion = ConflictLoss(
        w_direct=config.get('w_direct', 0.4),
        w_indirect=config.get('w_indirect', 0.3),
        w_implicit=config.get('w_implicit', 0.3)
    )
    
    scaler = torch.cuda.amp.GradScaler() if device == 'cuda' else None
    
    best_val_loss = float('inf')
    patience_counter = 0
    max_patience = config.get('patience', 15)
    
    history = {
        'train': [],
        'val': []
    }
    
    print(f"Starting training for {config.get('epochs', 100)} epochs...")
    
    for epoch in range(config.get('epochs', 100)):
        train_losses = train_epoch(
            model, train_loader, optimizer, criterion, device, scaler
        )
        
        val_losses = evaluate(model, val_loader, criterion, device)
        
        scheduler.step(val_losses['total_loss'])
        
        history['train'].append(train_losses)
        history['val'].append(val_losses)
        
        print(f"Epoch {epoch+1}/{config.get('epochs', 100)}")
        print(f"  Train - Total: {train_losses['total_loss']:.4f}, "
              f"Direct: {train_losses['direct_loss']:.4f}, "
              f"Indirect: {train_losses['indirect_loss']:.4f}, "
              f"Implicit: {train_losses['implicit_loss']:.4f}")
        print(f"  Val   - Total: {val_losses['total_loss']:.4f}, "
              f"Direct Acc: {val_losses['direct_accuracy']:.4f}, "
              f"Indirect Acc: {val_losses['indirect_accuracy']:.4f}, "
              f"Implicit Acc: {val_losses['implicit_accuracy']:.4f}")
        
        if val_losses['total_loss'] < best_val_loss:
            best_val_loss = val_losses['total_loss']
            patience_counter = 0
            
            checkpoint_path = os.path.join(output_dir, 'best_model.pt')
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_loss': val_losses['total_loss'],
                'config': config
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
    
    return model, history


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default='config/drl_config.yaml')
    parser.add_argument('--output', type=str, default='checkpoints/conflict_detector')
    args = parser.parse_args()
    
    train(args.config, args.output)