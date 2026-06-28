"""M. A. Halim — owned AI model package."""

__version__ = "0.1.0"
MODEL_NAME = "M. A. Halim"
SHORT_NAME = "Halim"

from halim.scaffold import SCAFFOLD_HF, SCAFFOLD_MLX_4BIT  # noqa: E402

__all__ = ("MODEL_NAME", "SHORT_NAME", "SCAFFOLD_HF", "SCAFFOLD_MLX_4BIT")
