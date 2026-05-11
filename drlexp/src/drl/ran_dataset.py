#!/usr/bin/env python3
"""
RAN Dataset Generator
====================
Generates synthetic dataset for conflict detection in AI-RAN, following the approach
from the IEEE WCNC 2025 paper:
"Conflict Detection in AI-RAN: Efficient Interaction Learning and Autonomous Graph Reconstruction"

This generates a dataset with:
- Na = 4 AI agents (RL, RF, Rules, Energy)
- Np = 7 parameters (power, bandwidth, etc.)
- Nk = 4 KPIs (latency, throughput, energy, CVaR)
- L = 10,000 samples with Gaussian distribution

Author: GreenRAN Team - UFPA
"""

import numpy as np
import pandas as pd
from typing import Tuple, Dict
import os


class RANDatasetGenerator:
    """
    Generates synthetic RAN dataset for conflict detection.
    
    Based on the conflict model from [15] in the paper:
    "Toward Control and Coordination in Cognitive Autonomous Networks"
    """
    
    def __init__(self, 
                 num_samples: int = 10000,
                 num_agents: int = 4,
                 num_params: int = 7,
                 num_kpis: int = 4,
                 seed: int = 42):
        """
        Initialize dataset generator.
        
        Args:
            num_samples: Number of samples (L)
            num_agents: Number of AI agents (Na)
            num_params: Number of parameters (Np)
            num_kpis: Number of KPIs (Nk)
            seed: Random seed for reproducibility
        """
        self.num_samples = num_samples
        self.num_agents = num_agents
        self.num_params = num_params
        self.num_kpis = num_kpis
        self.seed = seed
        
        np.random.seed(seed)
        
        # Define entity names
        self.agent_names = ['RL_Agent', 'RF_Agent', 'Rules_Agent', 'Energy_Agent']
        self.param_names = ['TX_Power', 'PRB_Allocation', 'MCS_Index', 'Handover_Threshold', 
                           'Scheduling_Priority', 'Beamforming', 'Carrier_Frequency']
        self.kpi_names = ['Latency', 'Throughput', 'Energy_Consumption', 'CVaR']
        
    def generate_parameters(self) -> np.ndarray:
        """
        Generate parameter values Xp ∈ R(Np×L).
        
        Returns:
            Xp: Parameters matrix (num_params, num_samples)
        """
        # Mean and std for each parameter
        param_means = np.array([20, 50, 15, 0.8, 5, 10, 3.5])  # dBm, PRBs, index, threshold, priority, beams, GHz
        param_stds = np.array([5, 15, 5, 0.2, 2, 3, 0.5])
        
        Xp = np.zeros((self.num_params, self.num_samples))
        for i in range(self.num_params):
            Xp[i, :] = np.random.normal(param_means[i], param_stds[i], self.num_samples)
            
        return Xp
    
    def generate_kpis(self, Xp: np.ndarray) -> np.ndarray:
        """
        Generate KPI values Xk ∈ R(Nk×L) based on parameters.
        
        This models the relationships between parameters and KPIs.
        
        Args:
            Xp: Parameters matrix
            
        Returns:
            Xk: KPIs matrix (num_kpis, num_samples)
        """
        Xk = np.zeros((self.num_kpis, self.num_samples))
        
        # Define relationships (similar to conflict model paper)
        # KPIs: Latency, Throughput, Energy, CVaR
        
        # Latency: inversely related to power, PRBs
        Xk[0, :] = (50 - 0.3 * Xp[0, :] + 0.1 * Xp[1, :] + 
                     2 * Xp[3, :] + np.random.normal(0, 2, self.num_samples))
        
        # Throughput: positively related to PRBs, MCS
        Xk[1, :] = (10 + 0.2 * Xp[0, :] + 0.5 * Xp[1, :] + 
                     1.5 * Xp[2, :] + np.random.normal(0, 3, self.num_samples))
        
        # Energy: positively related to power, PRBs
        Xk[2, :] = (5 + 0.4 * Xp[0, :] + 0.1 * Xp[1, :] + 
                     0.5 * Xp[5, :] + np.random.normal(0, 1, self.num_samples))
        
        # CVaR: complex relationship (our target for GreenRAN)
        Xk[3, :] = (40 + 0.5 * Xp[0, :] - 0.2 * Xp[1, :] + 
                     3 * Xp[3, :] + 0.8 * Xk[0, :] + 
                     np.random.normal(0, 5, self.num_samples))
        
        # Ensure non-negative values
        Xk = np.maximum(Xk, 0)
        
        return Xk
    
    def generate_ground_truth_interactions(self) -> np.ndarray:
        """
        Generate ground truth interaction matrix Y ∈ {0, 1}(Np×Nk).
        
        This represents the true relationships between parameters and KPIs.
        
        Returns:
            Y: Ground truth interaction matrix
        """
        # Based on our relationships above:
        Y = np.array([
            # Latency  Throughput  Energy  CVaR
            [1,        1,         1,      1],   # TX_Power
            [1,        1,         1,      1],   # PRB_Allocation
            [0,        1,         0,      0],   # MCS_Index
            [1,        0,         0,      1],   # Handover_Threshold
            [0,        0,         0,      0],   # Scheduling_Priority
            [0,        0,         1,      0],   # Beamforming
            [0,        0,         0,      0],   # Carrier_Frequency
        ])
        
        return Y
    
    def generate_known_relationships(self) -> np.ndarray:
        """
        Generate known relationships Aknown ∈ {0, 1}(Na×(Np+Nk)).
        
        This encodes which agents control/subscribe to which parameters/KPIs.
        
        Returns:
            Aknown: Known relationship matrix
        """
        # Each agent has specific relationships
        # Rows: agents, Columns: parameters + KPIs
        
        Aknown = np.zeros((self.num_agents, self.num_params + self.num_kpis))
        
        # RL Agent: controls power, PRBs, subscribes to all KPIs
        Aknown[0, :self.num_params] = [1, 1, 0, 0, 0, 0, 0]  # controls
        Aknown[0, self.num_params:] = [1, 1, 1, 1]  # subscribes
        
        # RF Agent: controls MCS, subscribes to Latency, Throughput
        Aknown[1, :self.num_params] = [0, 0, 1, 0, 0, 0, 0]  # controls
        Aknown[1, self.num_params:] = [1, 1, 0, 0]  # subscribes
        
        # Rules Agent: controls Handover, subscribes to CVaR
        Aknown[2, :self.num_params] = [0, 0, 0, 1, 0, 0, 0]  # controls
        Aknown[2, self.num_params:] = [0, 0, 0, 1]  # subscribes
        
        # Energy Agent: controls Beamforming, subscribes to Energy
        Aknown[3, :self.num_params] = [0, 0, 0, 0, 0, 1, 0]  # controls
        Aknown[3, self.num_params:] = [0, 0, 1, 0]  # subscribes
        
        return Aknown
    
    def generate_dataset(self) -> Dict[str, np.ndarray]:
        """
        Generate complete dataset.
        
        Returns:
            Dictionary with all dataset components
        """
        print("[Dataset] Generating RAN dataset...")
        print(f"  Samples: {self.num_samples}")
        print(f"  Agents: {self.num_agents}")
        print(f"  Parameters: {self.num_params}")
        print(f"  KPIs: {self.num_kpis}")
        
        # Generate data
        Xp = self.generate_parameters()
        Xk = self.generate_kpis(Xp)
        Y = self.generate_ground_truth_interactions()
        Aknown = self.generate_known_relationships()
        
        dataset = {
            'Xp': Xp,  # Parameters (Np × L)
            'Xk': Xk,  # KPIs (Nk × L)
            'Y': Y,    # Ground truth interactions (Np × Nk)
            'Aknown': Aknown,  # Known relationships (Na × Np+Nk)
            'agent_names': self.agent_names,
            'param_names': self.param_names,
            'kpi_names': self.kpi_names,
            'metadata': {
                'num_samples': self.num_samples,
                'num_agents': self.num_agents,
                'num_params': self.num_params,
                'num_kpis': self.num_kpis,
                'seed': self.seed
            }
        }
        
        print("[Dataset] Generation complete!")
        return dataset
    
    def save_dataset(self, output_dir: str = './data'):
        """
        Generate and save dataset to files.
        
        Args:
            output_dir: Directory to save dataset files
        """
        os.makedirs(output_dir, exist_ok=True)
        
        dataset = self.generate_dataset()
        
        # Save as numpy arrays
        np.save(os.path.join(output_dir, 'Xp.npy'), dataset['Xp'])
        np.save(os.path.join(output_dir, 'Xk.npy'), dataset['Xk'])
        np.save(os.path.join(output_dir, 'Y.npy'), dataset['Y'])
        np.save(os.path.join(output_dir, 'Aknown.npy'), dataset['Aknown'])
        
        # Save metadata as JSON
        import json
        with open(os.path.join(output_dir, 'metadata.json'), 'w') as f:
            json.dump({
                'agent_names': dataset['agent_names'],
                'param_names': dataset['param_names'],
                'kpi_names': dataset['kpi_names'],
                'metadata': dataset['metadata']
            }, f, indent=2)
        
        print(f"[Dataset] Saved to {output_dir}/")
        
        return dataset
    
    def to_dataframe(self, dataset: Dict) -> pd.DataFrame:
        """
        Convert dataset to pandas DataFrame for easier analysis.
        
        Args:
            dataset: Dataset dictionary
            
        Returns:
            DataFrame with parameters and KPIs
        """
        data = {}
        
        # Add parameters
        for i, name in enumerate(self.param_names):
            data[name] = dataset['Xp'][i, :]
            
        # Add KPIs
        for i, name in enumerate(self.kpi_names):
            data[name] = dataset['Xk'][i, :]
            
        return pd.DataFrame(data)


def load_dataset(data_dir: str = './data') -> Dict[str, np.ndarray]:
    """
    Load dataset from files.
    
    Args:
        data_dir: Directory with dataset files
        
    Returns:
        Dictionary with dataset components
    """
    import json
    
    dataset = {}
    dataset['Xp'] = np.load(os.path.join(data_dir, 'Xp.npy'))
    dataset['Xk'] = np.load(os.path.join(data_dir, 'Xk.npy'))
    dataset['Y'] = np.load(os.path.join(data_dir, 'Y.npy'))
    dataset['Aknown'] = np.load(os.path.join(data_dir, 'Aknown.npy'))
    
    with open(os.path.join(data_dir, 'metadata.json'), 'r') as f:
        metadata = json.load(f)
        dataset['agent_names'] = metadata['agent_names']
        dataset['param_names'] = metadata['param_names']
        dataset['kpi_names'] = metadata['kpi_names']
        dataset['metadata'] = metadata['metadata']
    
    return dataset


# Standalone test
if __name__ == "__main__":
    # Generate dataset
    generator = RANDatasetGenerator(
        num_samples=10000,
        num_agents=4,
        num_params=7,
        num_kpis=4,
        seed=42
    )
    
    print("=" * 60)
    print("RAN Dataset Generator Test")
    print("=" * 60)
    
    # Generate
    dataset = generator.generate_dataset()
    
    print(f"\nDataset shapes:")
    print(f"  Xp (parameters): {dataset['Xp'].shape}")
    print(f"  Xk (KPIs): {dataset['Xk'].shape}")
    print(f"  Y (ground truth): {dataset['Y'].shape}")
    print(f"  Aknown (known relationships): {dataset['Aknown'].shape}")
    
    print(f"\nGround truth interactions Y:")
    print(f"        ", "  ".join(f"{k:>10}" for k in dataset['kpi_names']))
    for i, p in enumerate(dataset['param_names']):
        print(f"{p:>15}  ", "  ".join(f"{v:>10}" for v in dataset['Y'][i, :]))
    
    print(f"\nKnown relationships Aknown:")
    print(f"        ", "  ".join(f"{p:>8}" for p in dataset['param_names'][:4]), 
          "  ", "  ".join(f"{k:>8}" for k in dataset['kpi_names']))
    for i, a in enumerate(dataset['agent_names']):
        print(f"{a:>15}  ", "  ".join(f"{v:>8}" for v in dataset['Aknown'][i, :7]))
    
    # Save
    dataset = generator.save_dataset('./drlexp/data')
    
    # Load and verify
    loaded = load_dataset('./drlexp/data')
    print(f"\nLoaded dataset shapes:")
    print(f"  Xp: {loaded['Xp'].shape}, Xk: {loaded['Xk'].shape}")
    
    print("\n✅ Dataset generator test passed!")