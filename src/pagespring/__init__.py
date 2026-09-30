"""pagespring — the lean manual acquisition + normalization layer.

Point it at a manual's URL; it recognizes the source type (a "pattern"),
*acquires* the raw pages, and *normalizes* them into one clean HTML/markdown
file with absolute asset URLs under ``incoming/<slug>/``. That clean file is the
deliverable, and pagespring stops there.
"""

__version__ = "0.13.0"
