"""pagespring — the lean manual acquisition + normalization layer.

Point it at a manual's URL; it recognizes the source type (a "pattern"),
*acquires* the raw pages, and *normalizes* them into one clean HTML/markdown
file with absolute asset URLs under ``incoming/<slug>/``. That clean file is the
deliverable; *conversion* to RAG markdown is a separate concern (**pagespeak**)
that consumes ``incoming/`` independently — this package neither runs nor imports it.

This package stays dependency-light (pf-core[cli] + beautifulsoup4).
"""

__version__ = "0.1.1"
