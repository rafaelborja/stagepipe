"""stagepipe: staged pipelines for blocking code. See the README."""
from .pipeline import Cancelled, Stage, run

__all__ = ["Cancelled", "Stage", "run"]
__version__ = "0.0.3"
