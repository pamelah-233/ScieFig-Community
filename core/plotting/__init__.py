"""Plotting entry points; importing style data does not initialize renderers."""

__all__ = ["build_qpcr_figure"]


def __getattr__(name):
    if name == "build_qpcr_figure":
        from .qpcr_plot import build_qpcr_figure

        globals()[name] = build_qpcr_figure
        return build_qpcr_figure
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
