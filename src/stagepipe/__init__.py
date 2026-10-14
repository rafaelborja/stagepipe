"""stagepipe: staged pipelines for blocking code. See the README."""
from .pipeline import (UNFINISHED, Cancelled, Ctx, Current, Fail, Failed, Permanent, RecoveryError, Rerun, Skip,
                       Stage, StageStats, Stats, Transient, current, run)

__all__ = ["UNFINISHED", "Cancelled", "Ctx", "Current", "Fail", "Failed", "Permanent", "RecoveryError", "Rerun",
           "Skip", "Stage", "StageStats", "Stats", "Transient", "current", "run"]
__version__ = "0.1.1"
