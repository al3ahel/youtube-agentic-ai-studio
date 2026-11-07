"""Optional video backends; importing this package does not start Node or MoviePy."""

from .html import (
    MotionRenderCancelled,
    MotionRenderError,
    prepare_motion_project,
    render_motion_video,
)

__all__ = [
    "MotionRenderCancelled", "MotionRenderError",
    "prepare_motion_project", "render_motion_video",
]
