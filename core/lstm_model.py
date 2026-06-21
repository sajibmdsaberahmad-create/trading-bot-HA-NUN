#!/usr/bin/env python3
"""
core/lstm_model.py — TensorFlow/Keras LSTM-based time-series model.

ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════
This implements a Bidirectional LSTM with attention mechanism:

1. Input Layer
   - Accepts sequences of feature vectors
   - Handles variable-length windows

2. Bidirectional LSTM Layers (×3)
   - Captures temporal patterns forward and backward
   - Stacked for hierarchical pattern extraction
   - L1/L2 regularization to prevent overfitting

3. Temporal Attention Layer
   - Learns which time steps are most important
   - Generates context-weighted representation

4. Dense Prediction Heads
   - Action head: HOLD/BUY/SELL classification
   - Value head: State value estimation (for RL)

WHY LSTM + TENSORFLOW?
- Provides ensemble diversity (different architecture learns different patterns)
- Bidirectional context helps identify regime changes
- TensorFlow's production deployment tools (TF Serving, TFLite)
- Complementary to Transformer: LSTM excels at local sequential patterns
"""

import numpy as np
from typing import Optional, Tuple, Dict
from dataclasses import dataclass

# TensorFlow/Keras imports with fallback
try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers, regularizers, optimizers, callbacks
    TF_AVAILABLE = True
    
    # Enable mixed precision for faster training on modern GPUs
    # from tensorflow.keras import mixed_precision
    # mixed_precision.set_global_policy('mixed_float16')
    
except ImportError:
    TF_AVAILABLE = False
    raise ImportError("TensorFlow is required for LSTM model. Install with: pip install tensorflow")


@dataclass
class LSTMConfig:
    """Configuration for the LSTM model."""
    
    # Architecture
    input_dim: int = 18           # Number of features
    seq_length: int = 60          # Sequence length (bars)
    
    # LSTM layers
    lstm_units: int = 128         # Units per LSTM layer
    lstm_layers: int = 3          # Number of LSTM layers
    bidirectional: bool = True    # Use bidirectional LSTM
    
    # Regularization
    dropout: float = 0.2          # Dropout rate
    recurrent_dropout: float = 0.2
    l2_reg: float = 1e-5          # L2 regularization
    
    # Attention
    use_attention: bool = True    # Enable temporal attention
    
    # Dense layers
    dense_units: int = 64         # Dense layer size
    
    # Output
    num_actions: int = 3          # HOLD, BUY, SELL
    
    # Training
    learning_rate: float = 1e-3
    batch_size: int = 64
    epochs: int = 50
    validation_split: float = 0.2
    
    # Device
    device: str = "auto"          # "auto", "gpu", "cpu"


class TemporalAttentionLayer(layers.Layer):
    """
    Temporal attention mechanism for focusing on important time steps.
    
    Computes attention weights for each time step and creates
    a context vector as weighted sum of LSTM outputs.
    """
    
    def __init__(self, units: int = 64, **kwargs):
        super().__init__(**kwargs)
        self.units = units
        
        # Attention layers
        self.W = self.add_weight(
            shape=(units, units),
            initializer='glorot_uniform',
            name='attention_W'
        )
        self.b = self.add_weight(
            shape=(units,),
            initializer='zeros',
            name='attention_b'
        )
        self.v = self.add_weight(
            shape=(units, 1),
            initializer='glorot_uniform',
            name='attention_v'
        )
    
    def call(self, lstm_output: tf.Tensor) -> Tuple[tf.Tensor, tf.Tensor]:
        """
        Apply attention to LSTM outputs.
        
        Args:
            lstm_output: (batch_size, seq_len, lstm_units)
            
        Returns:
            context: (batch_size, lstm_units)
            attention_weights: (batch_size, seq_len, 1)
        """
        # Calculate attention scores
        # score = tanh(W * h + b) . v
        score = tf.tanh(tf.tensordot(lstm_output, self.W, axes=([2], [0])) + self.b)
        score = tf.tensordot(score, self.v, axes=([2], [0]))  # (batch, seq_len, 1)
        
        # Softmax over time dimension
        attention_weights = tf.nn.softmax(score, axis=1)  # (batch, seq_len, 1)
        
        # Weighted sum of LSTM outputs
        context = tf.reduce_sum(lstm_output * attention_weights, axis=1)  # (batch, lstm_units)
        
        return context, attention_weights
    
    def get_config(self):
        config = super().get_config()
        config.update({'units': self.units})
        return config


class BuildLSTMModel(keras.Model):
    """
    Bidirectional LSTM with attention for trading.
    
    This is a Keras Model subclass for easier integration with
    TensorFlow's training and deployment ecosystem.
    """
    
    def __init__(self, config: LSTMConfig, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        
        # Input projection (if input_dim != lstm_units)
        self.input_projection = layers.Dense(
            config.lstm_units,
            activation='linear',
            name='input_projection'
        ) if config.input_dim != config.lstm_units else None
        
        # LSTM layers
        self.lstm_layers = []
        for i in range(config.lstm_layers):
            return_seq = i < config.lstm_layers - 1  # Return sequences for all but last
            
            lstm = layers.LSTM(
                config.lstm_units,
                return_sequences=return_seq,
                return_state=return_seq,
                dropout=config.dropout,
                recurrent_dropout=config.recurrent_dropout,
                kernel_regularizer=regularizers.l2(config.l2_reg),
                recurrent_regularizer=regularizers.l2(config.l2_reg),
                name=f'lstm_{i}'
            )
            
            if config.bidirectional:
                lstm = layers.Bidirectional(lstm, name=f'bilstm_{i}')
            
            self.lstm_layers.append(lstm)
        
        # Optional attention layer
        self.attention = TemporalAttentionLayer(config.lstm_units * (2 if config.bidirectional else 1)) if config.use_attention else None
        
        # Dense layers
        self.dense = layers.Dense(
            config.dense_units,
            activation='relu',
            kernel_regularizer=regularizers.l2(config.l2_reg),
            name='dense'
        )
        self.dropout = layers.Dropout(config.dropout)
        
        # Output heads
        self.action_head = layers.Dense(
            config.num_actions,
            activation='linear',
            name='action_head'
        )
        
        self.value_head = layers.Dense(
            1,
            activation='linear',
            name='value_head'
        )
    
    def call(self, inputs: tf.Tensor, training: bool = False) -> Tuple[tf.Tensor, tf.Tensor]:
        """
        Forward pass.
        
        Args:
            inputs: (batch_size, seq_len, input_dim)
            training: Training mode flag
            
        Returns:
            action_logits: (batch_size, num_actions)
            value: (batch_size, 1)
        """
        x = inputs
        
        # Project input if needed
        if self.input_projection is not None:
            # Apply projection to each time step
            x = tf.keras.layers.TimeDistributed(self.input_projection)(x)
        
        # Pass through LSTM layers
        for lstm in self.lstm_layers:
            x = lstm(x, training=training)
        
        # Apply attention if enabled
        if self.attention is not None:
            # Need to use the output from the last LSTM layer with sequences
            # Get all intermediate outputs
            lstm_outputs = []
            x_temp = inputs
            if self.input_projection is not None:
                x_temp = tf.keras.layers.TimeDistributed(self.input_projection)(x_temp)
            for lstm in self.lstm_layers[:-1]:
                x_temp = lstm(x_temp, training=training)
            
            # Last LSTM returns full sequences
            x_seq = self.lstm_layers[-1](x_temp, training=training)
            if hasattr(x_seq, '__len__'):
                x_seq = x_seq[0]  # Get output, not states
            
            context, _ = self.attention(x_seq)
            x = context
        else:
            # x is already the last output from LSTM
            pass
        
        # Dense layers
        x = self.dense(x)
        x = self.dropout(x, training=training)
        
        # Output heads
        action_logits = self.action_head(x)
        value = self.value_head(x)
        
        return action_logits, value
    
    def get_action_with_value(self, obs: np.ndarray, deterministic: bool = True) -> Tuple[int, float, np.ndarray]:
        """
        Get action and value from observation (matches PPO interface).
        
        Args:
            obs: Observation array (seq_len * input_dim + 2,)
            deterministic: Use greedy action selection
            
        Returns:
            action: Selected action
            value: State value estimate
            probs: Action probabilities
        """
        self.eval()
        
        # Extract window from flattened observation
        seq_len = self.config.seq_length
        input_dim = self.config.input_dim
        
        if len(obs) >= seq_len * input_dim + 2:
            window_flat = obs[:seq_len * input_dim]
            window = window_flat.reshape(1, seq_len, input_dim)
        else:
            # Pad if observation is too short
            window = np.zeros((1, seq_len, input_dim), dtype=np.float32)
            if len(obs) > 2:
                features = obs[:-2].reshape(1, -1, input_dim) if len(obs[:-2]) % input_dim == 0 else obs.reshape(1, -1)
                window[:, :features.shape[1]] = features
        
        # Forward pass
        with tf.GradientTape() as tape:
            action_logits, value = self(window, training=False)
        
        # Convert to probabilities
        probs = tf.nn.softmax(action_logits, axis=-1)
        
        if deterministic:
            action = int(tf.argmax(probs, axis=-1).numpy()[0])
        else:
            action = int(tf.random.categorical(probs, 1).numpy()[0, 0])
        
        return action, float(value.numpy()[0, 0]), probs.numpy().flatten()


class LSTMTrainer:
    """
    Training utilities for the LSTM model.
    """
    
    def __init__(self, model: BuildLSTMModel, config: LSTMConfig):
        self.model = model
        self.config = config
        
        self.optimizer = optimizers.Adam(learning_rate=config.learning_rate)
        
        self.loss_action = keras.losses.SparseCategoricalCrossentropy(from_logits=True)
        self.loss_value = keras.losses.MeanSquaredError()
        
        # Metrics
        self.metrics = {
            'action_loss': keras.metrics.Mean(name='action_loss'),
            'value_loss': keras.metrics.Mean(name='value_loss'),
            'total_loss': keras.metrics.Mean(name='total_loss'),
            'action_accuracy': keras.metrics.SparseCategoricalAccuracy(name='action_accuracy')
        }
    
    @tf.function
    def train_step(self, obs: tf.Tensor, actions: tf.Tensor, values: tf.Tensor) -> Dict[str, tf.Tensor]:
        """
        Single training step.
        
        Args:
            obs: (batch, seq_len, input_dim)
            actions: (batch,) action indices
            values: (batch, 1) state values
            
        Returns:
            Dict of losses
        """
        with tf.GradientTape() as tape:
            action_logits, value_pred = self.model(obs, training=True)
            
            loss_action = self.loss_action(actions, action_logits)
            loss_value = self.loss_value(values, value_pred)
            loss_total = loss_action + 0.5 * loss_value
        
        # Compute and apply gradients
        gradients = tape.gradient(loss_total, self.model.trainable_variables)
        # Clip gradients
        gradients = [tf.clip_by_value(g, -1.0, 1.0) for g in gradients]
        self.optimizer.apply_gradients(zip(gradients, self.model.trainable_variables))
        
        # Update metrics
        self.metrics['total_loss'].update_state(loss_total)
        self.metrics['action_loss'].update_state(loss_action)
        self.metrics['value_loss'].update_state(loss_value)
        self.metrics['action_accuracy'].update_state(actions, action_logits)
        
        return {
            'loss': loss_total,
            'action_loss': loss_action,
            'value_loss': loss_value
        }
    
    def fit(self, train_dataset, validation_dataset=None, epochs: int = None) -> Dict:
        """
        Train the model.
        
        Args:
            train_dataset: tf.data.Dataset for training
            validation_dataset: Optional validation dataset
            epochs: Number of epochs (uses config default if None)
            
        Returns:
            Training history
        """
        epochs = epochs or self.config.epochs
        
        # Reset metrics
        for metric in self.metrics.values():
            metric.reset_states()
        
        history = {
            'loss': [],
            'action_loss': [],
            'value_loss': [],
            'action_accuracy': [],
            'val_loss': [],
            'val_action_loss': [],
            'val_value_loss': [],
            'val_action_accuracy': []
        }
        
        for epoch in range(epochs):
            # Training
            for metric in self.metrics.values():
                metric.reset_states()
            
            for obs, actions, values in train_dataset:
                self.train_step(obs, actions, values)
            
            history['loss'].append(float(self.metrics['total_loss'].result()))
            history['action_loss'].append(float(self.metrics['action_loss'].result()))
            history['value_loss'].append(float(self.metrics['value_loss'].result()))
            history['action_accuracy'].append(float(self.metrics['action_accuracy'].result()))
            
            # Validation
            if validation_dataset is not None:
                val_loss = self._validate(validation_dataset)
                history['val_loss'].append(val_val := float(val_loss['total_loss']))
                history['val_action_loss'].append(float(val_loss['action_loss']))
                history['val_value_loss'].append(float(val_loss['value_loss']))
                history['val_action_accuracy'].append(float(val_loss['action_accuracy']))
            
            if epoch % 10 == 0:
                print(f"Epoch {epoch:3d}: loss={history['loss'][-1]:.4f}, "
                      f"acc={history['action_accuracy'][-1]:.4f}")
        
        return history
    
    @tf.function
    def _validate(self, dataset) -> Dict[str, tf.Tensor]:
        """Run validation."""
        val_losses = []
        val_action_losses = []
        val_value_losses = []
        val_accuracies = []
        
        for obs, actions, values in dataset:
            action_logits, value_pred = self.model(obs, training=False)
            
            loss_action = self.loss_action(actions, action_logits)
            loss_value = self.loss_value(values, value_pred)
            loss_total = loss_action + 0.5 * loss_value
            
            val_losses.append(loss_total)
            val_action_losses.append(loss_action)
            val_value_losses.append(loss_value)
            
            # Accuracy
            pred_actions = tf.argmax(action_logits, axis=-1)
            accuracy = tf.reduce_mean(tf.cast(tf.equal(pred_actions, actions), tf.float32))
            val_accuracies.append(accuracy)
        
        return {
            'total_loss': tf.reduce_mean(val_losses),
            'action_loss': tf.reduce_mean(val_action_losses),
            'value_loss': tf.reduce_mean(val_value_losses),
            'action_accuracy': tf.reduce_mean(val_accuracies)
        }
    
    def save(self, path: str):
        """Save model weights."""
        self.model.save_weights(path)
    
    def load(self, path: str):
        """Load model weights."""
        self.model.load_weights(path)


def create_lstm(config: Optional[LSTMConfig] = None) -> Tuple[BuildLSTMModel, LSTMTrainer]:
    """
    Factory function to create LSTM model.
    
    Args:
        config: LSTMConfig (uses defaults if None)
        
    Returns:
        model: BuildLSTMModel instance
        trainer: LSTMTrainer instance
    """
    if not TF_AVAILABLE:
        raise ImportError("TensorFlow not available")
    
    if config is None:
        config = LSTMConfig()
    
    # Set memory growth for GPU
    if config.device == "auto" or config.device == "gpu":
        gpus = tf.config.experimental.list_physical_devices('GPU')
        if gpus:
            try:
                for gpu in gpus:
                    tf.config.experimental.set_memory_growth(gpu, True)
            except RuntimeError:
                pass
    
    model = BuildLSTMModel(config)
    trainer = LSTMTrainer(model, config)
    
    return model, trainer


def prepare_lstm_dataset(features: np.ndarray, prices: np.ndarray,
                        seq_length: int = 60, 
                        lookahead: int = 1) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Prepare sequential dataset for LSTM training.
    
    Creates sequences of length seq_length and labels for next lookahead bars.
    
    Args:
        features: Feature matrix (n_samples, n_features)
        prices: Price array (n_samples,)
        seq_length: Length of input sequences
        lookahead: How many bars ahead to predict
        
    Returns:
        X: Sequences (n_sequences, seq_length, n_features)
        actions: Action labels (n_sequences,)
        values: Value targets (n_sequences,)
    """
    n_samples = len(features)
    n_features = features.shape[1]
    
    X = []
    actions = []
    values = []
    
    for i in range(seq_length, n_samples - lookahead):
        # Sequence of features
        seq = features[i - seq_length:i]
        X.append(seq)
        
        # Label: action based on future price movement
        current_price = prices[i - 1]
        future_price = prices[i + lookahead - 1]
        
        pct_change = (future_price / current_price - 1) * 100
        
        if pct_change > 0.5:
            action = 1  # BUY
        elif pct_change < -0.5:
            action = 2  # SELL
        else:
            action = 0  # HOLD
        
        actions.append(action)
        
        # Value target: future return
        value = pct_change / 100.0
        values.append(value)
    
    return (
        np.array(X, dtype=np.float32),
        np.array(actions, dtype=np.int32),
        np.array(values, dtype=np.float32).reshape(-1, 1)
    )


# Convenience function for inference (used by agent ensemble)
def predict_with_lstm(model: BuildLSTMModel, obs: np.ndarray,
                      config: LSTMConfig, deterministic: bool = True) -> Tuple[int, float, np.ndarray]:
    """
    Wrapper for LSTM prediction (matches PPO/Transformer interface).
    
    Args:
        model: LSTM model
        obs: Observation array (seq_len * input_dim + 2,)
        config: Model configuration
        deterministic: Use greedy action selection
        
    Returns:
        action, value, probabilities
    """
    return model.get_action_with_value(obs, deterministic)