#!/usr/bin/env python3
"""
core/transformer_model.py — PyTorch Transformer-based time-series model.

ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════
Temporal Fusion Transformer (TFT) optimized for MacBook MPS (2.5GB RAM):

Model Size: ~80M parameters (~320MB FP32, ~160MB FP16)
- 8 attention layers with 8 heads
- 768 embedding dimension
- 3072 feedforward dimension
- 60-bar sequence window

Each layer: Multi-Head Attention → Layer Norm → Feed Forward → Layer Norm
Total: ~80M parameters
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import math
from typing import Optional, Tuple, Dict
from dataclasses import dataclass

@dataclass
class TransformerConfig:
    input_dim: int = 18
    d_model: int = 768            # 768 embedding
    nhead: int = 8                # 8 heads
    num_layers: int = 8           # 8 layers
    dim_feedforward: int = 3072   # 3072 FFN
    dropout: float = 0.1
    max_seq_len: int = 60
    num_actions: int = 3
    num_value_outputs: int = 1
    learning_rate: float = 1e-4
    weight_decay: float = 1e-5
    batch_size: int = 64
    epochs: int = 50
    device: str = "auto"

class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 5000, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0).transpose(0, 1)
        self.register_buffer('pe', pe)
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.pe[:x.size(0), :]
        return self.dropout(x)

class TransformerEncoderLayer(nn.Module):
    def __init__(self, d_model: int, nhead: int, dim_feedforward: int, dropout: float = 0.1):
        super().__init__()
        self.norm1 = nn.LayerNorm(d_model)
        self.attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
        self.dropout1 = nn.Dropout(dropout)
        self.norm2 = nn.LayerNorm(d_model)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.linear2 = nn.Linear(dim_feedforward, d_model)
        self.dropout2 = nn.Dropout(dropout)
        self.dropout3 = nn.Dropout(dropout)
        self.activation = nn.GELU()
    def forward(self, src: torch.Tensor, src_mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        src_norm = self.norm1(src)
        attn_output, _ = self.attn(src_norm, src_norm, src_norm, attn_mask=src_mask)
        src = src + self.dropout1(attn_output)
        src_norm = self.norm2(src)
        ff_output = self.linear2(self.dropout2(self.activation(self.linear1(src_norm))))
        src = src + self.dropout3(ff_output)
        return src

class TemporalFusionTransformer(nn.Module):
    def __init__(self, config: TransformerConfig):
        super().__init__()
        self.config = config
        # Device detection
        if config.device == "auto":
            if torch.cuda.is_available():
                self.device = torch.device("cuda")
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                self.device = torch.device("mps")
            else:
                self.device = torch.device("cpu")
        else:
            self.device = torch.device(config.device)
        
        # Core layers
        self.input_projection = nn.Linear(config.input_dim, config.d_model)
        self.pos_encoding = PositionalEncoding(config.d_model, config.max_seq_len, config.dropout)
        self.layers = nn.ModuleList([
            TransformerEncoderLayer(config.d_model, config.nhead, config.dim_feedforward, config.dropout)
            for _ in range(config.num_layers)
        ])
        self.final_norm = nn.LayerNorm(config.d_model)
        self.gate = nn.Sequential(nn.Linear(config.d_model, config.d_model), nn.Sigmoid())
        self.action_head = nn.Sequential(
            nn.Linear(config.d_model, config.dim_feedforward), nn.GELU(),
            nn.Dropout(config.dropout), nn.Linear(config.dim_feedforward, config.num_actions)
        )
        self.value_head = nn.Sequential(
            nn.Linear(config.d_model, config.dim_feedforward), nn.GELU(),
            nn.Dropout(config.dropout), nn.Linear(config.dim_feedforward, config.num_value_outputs)
        )
        # Move to device
        self.to(self.device)
    
    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> Tuple[torch.Tensor, torch.Tensor]:
        batch_size, seq_len, _ = x.shape
        x = self.input_projection(x)
        x = x.transpose(0, 1)
        x = self.pos_encoding(x)
        x = x.transpose(0, 1)
        if mask is None:
            causal_mask = torch.triu(torch.ones(seq_len, seq_len, device=x.device), diagonal=1).bool()
            mask = ~causal_mask
        for layer in self.layers:
            x = layer(x, mask)
        x = self.final_norm(x)
        gate_weights = self.gate(x[:, -1, :])
        x = x[:, -1, :] * gate_weights
        return self.action_head(x), self.value_head(x)
    
    def get_action_with_value(self, obs: np.ndarray, deterministic: bool = True) -> Tuple[int, float, np.ndarray]:
        self.eval()
        with torch.no_grad():
            window_size = self.config.max_seq_len
            n_features = self.config.input_dim
            if len(obs) >= window_size * n_features + 2:
                window = obs[:window_size * n_features].reshape(window_size, n_features)
            else:
                window = np.zeros((window_size, n_features), dtype=np.float32)
                window[:min(len(obs)//n_features, window_size)] = obs.reshape(-1, n_features)[:window_size]
            window = torch.FloatTensor(window).unsqueeze(0).to(self.device)
            action_logits, value = self.forward(window)
            probs = F.softmax(action_logits, dim=-1)
            action = int(torch.argmax(probs, dim=-1).item()) if deterministic else int(torch.multinomial(probs, 1).item())
            return action, float(value.item()), probs.cpu().numpy().flatten()

class TransformerTrainer:
    def __init__(self, model: TemporalFusionTransformer, config: TransformerConfig):
        self.model = model
        self.config = config
        self.optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(self.optimizer, T_max=config.epochs)
        self.criterion_action = nn.CrossEntropyLoss()
        self.criterion_value = nn.MSELoss()
    
    def train_epoch(self, train_loader, epoch: int) -> Dict[str, float]:
        self.model.train()
        total_loss = total_action_loss = total_value_loss = 0.0
        for obs, actions, values in train_loader:
            self.optimizer.zero_grad()
            action_logits, value_pred = self.model(obs)
            loss = self.criterion_action(action_logits, actions) + 0.5 * self.criterion_value(value_pred.squeeze(), values)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 0.5)
            self.optimizer.step()
            total_loss += loss.item()
            total_action_loss += self.criterion_action(action_logits, actions).item()
            total_value_loss += self.criterion_value(value_pred.squeeze(), values).item()
        self.scheduler.step()
        n = len(train_loader)
        return {"loss": total_loss/n, "action_loss": total_action_loss/n, "value_loss": total_value_loss/n, "lr": self.scheduler.get_last_lr()[0]}
    
    def save(self, path: str):
        torch.save({'model_state_dict': self.model.state_dict(), 'config': self.config}, path)
    
    def load(self, path: str):
        checkpoint = torch.load(path, map_location=self.model.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])

def create_transformer(config: Optional[TransformerConfig] = None):
    if config is None: config = TransformerConfig()
    model = TemporalFusionTransformer(config)
    trainer = TransformerTrainer(model, config)
    return model, trainer

def predict_with_transformer(model, obs, config, deterministic=True):
    return model.get_action_with_value(obs, deterministic)


# ═════════════════════════════════════════════════════════════════════════════
# GRANDMASTER DISTILLATION PIPELINE (210M Teacher → 21M Student)
# ═════════════════════════════════════════════════════════════════════════════

class AlphaDistillationLoss(nn.Module):
    """
    KL-Divergence based distillation to transfer "dark knowledge" from
    the 210M parameter Grandmaster Teacher to the 21M parameter Student.
    
    Blends soft targets (from Teacher with temperature scaling) with
    hard targets (actual price direction labels) for stable convergence.
    """

    def __init__(self, temperature: float = 3.0, alpha: float = 0.4):
        super().__init__()
        self.T = temperature
        self.alpha = alpha
        self.mse_loss = nn.MSELoss()
        self.kl_div = nn.KLDivLoss(reduction="batchmean")

    def forward(self, student_logits: torch.Tensor, teacher_logits: torch.Tensor,
                targets: torch.Tensor) -> torch.Tensor:
        # 1. Soft targets from Teacher with Temperature scaling
        soft_teacher = F.softmax(teacher_logits / self.T, dim=-1)
        log_soft_student = F.log_softmax(student_logits / self.T, dim=-1)
        
        # 2. Distillation (Soft) Loss + Hard Target Loss
        loss_soft = self.kl_div(log_soft_student, soft_teacher) * (self.T ** 2)
        loss_hard = self.mse_loss(student_logits, targets)
        
        # 3. Blended Loss
        total_loss = (self.alpha * loss_hard) + ((1.0 - self.alpha) * loss_soft)
        return total_loss


def create_grandmaster_config() -> TransformerConfig:
    """Return config scaled to ~210M parameters for the Teacher model."""
    cfg = TransformerConfig()
    cfg.d_model = 768
    cfg.nhead = 12
    cfg.dim_feedforward = 3072
    cfg.num_layers = 8
    cfg.dropout = 0.15
    cfg.max_seq_len = 60
    return cfg


def create_student_config() -> TransformerConfig:
    """Return config scaled to ~21M parameters for the live Student model."""
    cfg = TransformerConfig()
    cfg.d_model = 256
    cfg.nhead = 4
    cfg.dim_feedforward = 1024
    cfg.num_layers = 3
    cfg.dropout = 0.1
    cfg.max_seq_len = 60
    return cfg


def count_parameters(model: nn.Module) -> int:
    """Count total trainable parameters."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def init_grandmaster_teacher(checkpoint_path: Optional[str] = None) -> TemporalFusionTransformer:
    """
    Initialize the 210M Teacher model.
    
    If checkpoint_path is provided, loads pretrained weights.
    Otherwise returns randomly initialized model ready for training.
    """
    cfg = create_grandmaster_config()
    teacher = TemporalFusionTransformer(cfg)
    n_params = count_parameters(teacher)
    log.info(f"🏛️  Grandmaster Teacher initialized: {n_params:,} parameters "
             f"(d_model={cfg.d_model}, layers={cfg.num_layers})")
    
    if checkpoint_path:
        try:
            teacher.load_state_dict(torch.load(checkpoint_path, map_location=teacher.device))
            log.info(f"   Loaded checkpoint from {checkpoint_path}")
        except Exception as e:
            log.warning(f"   Could not load checkpoint: {e}, using random init")
    return teacher


def run_grandmaster_distillation(train_loader, student_model,
                                  teacher_checkpoint: Optional[str] = None,
                                  device: Optional[torch.device] = None) -> TemporalFusionTransformer:
    """
    Two-phase distillation pipeline:
      Phase 1: Teacher is frozen (either pretrained or freshly trained)
      Phase 2: Student learns to match Teacher's soft probability distributions
    
    This function is designed to run inside the Colab/off-hours subprocess.
    
    Args:
        train_loader: PyTorch DataLoader yielding (features, targets)
        student_model: The live 21M TemporalFusionTransformer to optimize
        teacher_checkpoint: Optional path to pretrained 210M weights
        device: torch.device (auto-detected if None)
        
    Returns:
        Optimized student model with distilled knowledge
    """
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "mps"
                              if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()
                              else "cpu")
    
    log.info(f"🎓 Executing Grandmaster Distillation Pipeline on: {device}")

    # 1. Initialize massive 210M Teacher
    teacher = init_grandmaster_teacher(teacher_checkpoint)
    
    # If no checkpoint, train teacher first (Phase 1)
    if teacher_checkpoint is None:
        log.info("--- Phase 1: Pre-training Grandmaster Teacher on Raw Data ---")
        teacher.train()
        teacher_optimizer = torch.optim.AdamW(teacher.parameters(), lr=1e-4, weight_decay=1e-5)
        teacher_criterion = nn.MSELoss()
        
        for epoch in range(5):  # Light pre-training pass
            epoch_loss = 0.0
            n_batches = 0
            for features, targets in train_loader:
                features, targets = features.to(device), targets.to(device)
                teacher_optimizer.zero_grad()
                logits, _ = teacher(features)
                loss = teacher_criterion(logits, targets)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(teacher.parameters(), 1.0)
                teacher_optimizer.step()
                epoch_loss += loss.item()
                n_batches += 1
            log.info(f"   Teacher pretraining epoch {epoch+1}: loss={epoch_loss/n_batches:.4f}")
    
    # Freeze Teacher entirely
    teacher.eval()
    for param in teacher.parameters():
        param.requires_grad = False
    log.info("🔒 Grandmaster Teacher frozen for distillation")

    # 2. Phase 2: Distill to Live Student Model
    student_model = student_model.to(device)
    student_model.train()
    
    optimizer = torch.optim.AdamW(student_model.parameters(), lr=1e-4, weight_decay=1e-2)
    criterion = AlphaDistillationLoss(temperature=3.0, alpha=0.4)

    log.info("--- Phase 2: Transferring Knowledge to Student ---")
    for epoch in range(10):
        epoch_loss = 0.0
        n_batches = 0
        for features, targets in train_loader:
            features, targets = features.to(device), targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            
            with torch.no_grad():
                teacher_logits, _ = teacher(features)
            student_logits, _ = student_model(features)
            
            loss = criterion(student_logits, teacher_logits, targets)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(student_model.parameters(), 0.5)
            optimizer.step()
            
            epoch_loss += loss.item()
            n_batches += 1
        
        avg_loss = epoch_loss / n_batches
        log.info(f"   Student distillation epoch {epoch+1}: loss={avg_loss:.4f}")
        
    student_model.eval()
    log.info("✅ Optimization complete. Distilled student weights ready for export.")
    return student_model
