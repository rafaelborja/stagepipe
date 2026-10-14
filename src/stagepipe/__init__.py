"""stagepipe: staged pipelines for blocking code. See the README."""
from .pipeline import UNFINISHED, Cancelled, Current, Failed, Stage, Stats, StageStats, current, run

__all__ = ["UNFINISHED", "Cancelled", "Current", "Failed", "Stage", "StageStats", "Stats", "current", "run"]
__version__ = "0.1.1"
