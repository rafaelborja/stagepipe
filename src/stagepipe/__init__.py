"""stagepipe: staged pipelines for blocking code. See the README."""
from .pipeline import UNFINISHED, Cancelled, Failed, Stage, Stats, StageStats, run

__all__ = ["UNFINISHED", "Cancelled", "Failed", "Stage", "StageStats", "Stats", "run"]
__version__ = "0.1.0"
