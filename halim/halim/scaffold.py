"""
Halim training scaffold — HuggingFace/MLX registry IDs only.

M. A. Halim is the owned product (see halim.protocol.MODEL_NAME). These constants
are the open-weight base used to load LoRA adapters during toddler training.
Never use scaffold names in user-facing logs or UI.
"""

from __future__ import annotations

# HF registry id — required by transformers/peft and checkpoint manifests.
SCAFFOLD_HF = "Qwen/Qwen2.5-0.5B-Instruct"
# MLX 4-bit quant of the same scaffold — default on Apple Silicon.
SCAFFOLD_MLX_4BIT = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
