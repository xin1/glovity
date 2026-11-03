"""TeleX SDK: unified tracker + glove session."""

from importlib.metadata import PackageNotFoundError, version

from telex.session import TeleXSession
from telex.types import (
    FrameResult,
    HandState,
    IMU_LABELS,
    JOINT_LABELS,
    TeleXError,
    TeleXStrictError,
)

try:
    __version__ = version("telex")
except PackageNotFoundError:  # pragma: no cover - editable / source tree
    __version__ = "0.3.0"

__all__ = [
    "FrameResult",
    "HandState",
    "IMU_LABELS",
    "JOINT_LABELS",
    "TeleXError",
    "TeleXSession",
    "TeleXStrictError",
    "__version__",
]
