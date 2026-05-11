"""
SBiLSTM Model (Stacked Bidirectional LSTM)
============================================
Implementation of Stacked Bidirectional LSTM for large-scale resource prediction.
Based on IEEE paper: "Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing"

The SBiLSTM is used for:
- Large time-scale prediction (Prediction Window)
- Predicting required resources for next PW based on historical data

Author: GreenRAN Team - UFPA
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional


class SBiLSTM(nn.Module):
    """
    Stacked Bidirectional LSTM for resource prediction.
    
    Architecture (based on paper):
        - Input: Sequence of historical resource data (PW × features)
        - Layer 1: Bidirectional LSTM (128 units)
        - Dropout (0.2)
        - Layer 2: Bidirectional LSTM (64 units)
        - Dropout (0.2)
        - Dense Layer (32 units, ReLU)
        - Output: Predicted resources for next PW
    
    The bidirectional approach allows the model to learn patterns
    from both past and future contexts, improving prediction accuracy.
    """
    
    def __init__(self, 
                 input_size: int = 18,
                 hidden_size_1: int = 128,
                 hidden_size_2: int = 64,
                 num_layers: int = 2,
                 dropout: float = 0.2,
                 output_size: int = 1):
        """
        Initialize SBiLSTM model.
        
        Args:
            input_size: Number of input features per timestep
            hidden_size_1: Number of hidden units in first LSTM layer
            hidden_size_2: Number of hidden units in second LSTM layer
            num_layers: Number of LSTM layers (stacked)
            dropout: Dropout probability
            output_size: Number of output features
        """
        super(SBiLSTM, self).__init__()
        
        self.input_size = input_size
        self.hidden_size_1 = hidden_size_1
        self.hidden_size_2 = hidden_size_2
        self.num_layers = num_layers
        self.dropout = dropout
        self.output_size = output_size
        
        # First Bidirectional LSTM layer
        self.lstm1 = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size_1,
            num_layers=1,
            batch_first=True,
            bidirectional=True
        )
        
        # Dropout after first LSTM
        self.dropout1 = nn.Dropout(dropout)
        
        # Second Bidirectional LSTM layer
        # Input size is doubled due to bidirectional (forward + backward)
        self.lstm2 = nn.LSTM(
            input_size=hidden_size_1 * 2,  # bidirectional output
            hidden_size=hidden_size_2,
            num_layers=1,
            batch_first=True,
            bidirectional=True
        )
        
        # Dropout after second LSTM
        self.dropout2 = nn.Dropout(dropout)
        
        # Dense layers for output
        # Input: hidden_size_2 * 2 (bidirectional) * sequence_length
        # We'll use the last hidden state instead for prediction
        dense_input_size = hidden_size_2 * 2  # Last output from both directions
        
        self.dense1 = nn.Linear(dense_input_size, 32)
        self.dense2 = nn.Linear(32, output_size)
        
    def forward(self, x: torch.Tensor, 
                hidden: Optional[Tuple[torch.Tensor, torch.Tensor]] = None) -> Tuple[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]:
        """
        Forward pass through SBiLSTM.
        
        Args:
            x: Input tensor of shape (batch, sequence_length, input_size)
            hidden: Optional tuple of (h_0, c_0) for LSTM hidden states
            
        Returns:
            Tuple of (output, (h_n, c_n))
            - output: Predicted resources (batch, output_size)
            - hidden: Final hidden states
        """
        batch_size = x.size(0)
        
        # Initialize hidden states if not provided
        if hidden is None:
            h0 = torch.zeros(2, batch_size, self.hidden_size_1)  # 2 for bidirectional
            c0 = torch.zeros(2, batch_size, self.hidden_size_1)
            h1 = torch.zeros(2, batch_size, self.hidden_size_2)
            c1 = torch.zeros(2, batch_size, self.hidden_size_2)
            hidden = ((h0, c0), (h1, c1))
        
        # First LSTM layer
        lstm1_out, (h1, c1) = self.lstm1(x, hidden[0])
        lstm1_out = self.dropout1(lstm1_out)
        
        # Second LSTM layer
        lstm2_out, (h2, c2) = self.lstm2(lstm1_out, hidden[1])
        lstm2_out = self.dropout2(lstm2_out)
        
        # Use last hidden state from both directions
        # h2 shape: (2, batch, hidden_size_2) - [forward, backward]
        # Concatenate forward and backward
        last_hidden = torch.cat([h2[0], h2[1]], dim=1)  # (batch, hidden_size_2 * 2)
        
        # Dense layers
        dense_out = F.relu(self.dense1(last_hidden))
        output = self.dense2(dense_out)  # (batch, output_size)
        
        return output, ((h1, c1), (h2, c2))
    
    def predict(self, x: torch.Tensor) -> torch.Tensor:
        """
        Make prediction without returning hidden states.
        
        Args:
            x: Input tensor of shape (batch, sequence_length, input_size)
            
        Returns:
            Predicted resources tensor
        """
        output, _ = self.forward(x)
        return output


class SBiLSTMPredictor(nn.Module):
    """
    Extended SBiLSTM for multiple output prediction.
    
    Used for predicting:
    - CVaR prediction
    - Resource allocation
    - Energy consumption
    """
    
    def __init__(self, 
                 input_size: int = 18,
                 hidden_sizes: Tuple[int, int] = (128, 64),
                 dropout: float = 0.2,
                 output_size: int = 3):
        """
        Initialize extended SBiLSTM predictor.
        
        Args:
            input_size: Number of input features
            hidden_sizes: Tuple of (first_layer_size, second_layer_size)
            dropout: Dropout probability
            output_size: Number of outputs (CVaR, resources, energy)
        """
        super(SBiLSTMPredictor, self).__init__()
        
        self.base_lstm = SBiLSTM(
            input_size=input_size,
            hidden_size_1=hidden_sizes[0],
            hidden_size_2=hidden_sizes[1],
            dropout=dropout,
            output_size=output_size
        )
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass returning multiple predictions."""
        return self.base_lstm.predict(x)


class AttentionSBiLSTM(nn.Module):
    """
    SBiLSTM with Attention mechanism for better feature weighting.
    
    The attention mechanism allows the model to focus on the most
    relevant parts of the input sequence for prediction.
    """
    
    def __init__(self, 
                 input_size: int = 18,
                 hidden_size: int = 128,
                 num_layers: int = 2,
                 dropout: float = 0.2,
                 output_size: int = 1):
        """
        Initialize Attention-based SBiLSTM.
        
        Args:
            input_size: Number of input features
            hidden_size: Hidden layer size
            num_layers: Number of LSTM layers
            dropout: Dropout probability
            output_size: Number of outputs
        """
        super(AttentionSBiLSTM, self).__init__()
        
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        
        # Bidirectional LSTM
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0
        )
        
        # Attention mechanism
        self.attention = nn.Linear(hidden_size * 2, 1)
        
        # Output layer
        self.output = nn.Linear(hidden_size * 2, output_size)
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass with attention.
        
        Args:
            x: Input tensor (batch, seq_len, input_size)
            
        Returns:
            Predicted output
        """
        # LSTM pass
        lstm_out, _ = self.lstm(x)  # (batch, seq_len, hidden_size * 2)
        
        # Attention weights
        attention_weights = F.softmax(self.attention(lstm_out), dim=1)  # (batch, seq_len, 1)
        
        # Apply attention
        context = torch.sum(attention_weights * lstm_out, dim=1)  # (batch, hidden_size * 2)
        
        # Output
        output = self.output(context)
        
        return output


def create_sbilstm_model(config: dict) -> SBiLSTM:
    """
    Factory function to create SBiLSTM model from config.
    
    Args:
        config: Dictionary with model configuration
        
    Returns:
        SBiLSTM model instance
    """
    return SBiLSTM(
        input_size=config.get('input_size', 18),
        hidden_size_1=config.get('hidden_size_1', 128),
        hidden_size_2=config.get('hidden_size_2', 64),
        num_layers=config.get('num_layers', 2),
        dropout=config.get('dropout', 0.2),
        output_size=config.get('output_size', 1)
    )


# Standalone test
if __name__ == "__main__":
    # Create model
    model = SBiLSTM(
        input_size=18,
        hidden_size_1=128,
        hidden_size_2=64,
        output_size=1
    )
    
    print("=" * 60)
    print("SBiLSTM Model Test")
    print("=" * 60)
    
    # Print model architecture
    print("\nModel Architecture:")
    print(model)
    
    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"\nTotal parameters: {total_params:,}")
    print(f"Trainable parameters: {trainable_params:,}")
    
    # Test forward pass
    # Input: batch=32, sequence=1200 (PW), features=18
    batch_size = 32
    seq_length = 1200
    input_features = 18
    
    x = torch.randn(batch_size, seq_length, input_features)
    
    print(f"\nInput shape: {x.shape}")
    
    # Forward pass
    output, hidden = model(x)
    
    print(f"Output shape: {output.shape}")
    print(f"Hidden state shapes: h1={hidden[0][0].shape}, h2={hidden[1][0].shape}")
    
    # Test prediction only
    pred = model.predict(x)
    print(f"Prediction shape: {pred.shape}")
    
    # Test with different batch sizes
    for bs in [1, 16, 64]:
        x_test = torch.randn(bs, seq_length, input_features)
        out = model.predict(x_test)
        print(f"Batch {bs}: output shape = {out.shape}")
    
    # Test AttentionSBiLSTM
    print("\n" + "=" * 60)
    print("Attention SBiLSTM Test")
    print("=" * 60)
    
    att_model = AttentionSBiLSTM(input_size=18, hidden_size=128)
    x_att = torch.randn(32, 1200, 18)
    out_att = att_model(x_att)
    print(f"Attention model output shape: {out_att.shape}")
    
    print("\n✅ SBiLSTM model tests passed!")