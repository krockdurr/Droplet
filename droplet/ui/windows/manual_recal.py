"""ManualRecalWindow and PeakReviewWindow - manual recalibration workflow.

These classes are defined in droplet/app.py for now because they are tightly
coupled to the main application state (plot, scatter items, batch state, etc.).

TODO: migrate ManualRecalWindow and PeakReviewWindow here once the main window
is refactored into a proper MainWindow(QWidget) class so state can be passed
explicitly rather than accessed through global variables.
"""

# Re-export from app so callers can do:
#   from droplet.ui.windows.manual_recal import ManualRecalWindow, PeakReviewWindow
# without knowing where the class actually lives.


def ManualRecalWindow(*args, **kwargs):
    from droplet.app import ManualRecalWindow as _cls
    return _cls(*args, **kwargs)


def PeakReviewWindow(*args, **kwargs):
    from droplet.app import PeakReviewWindow as _cls
    return _cls(*args, **kwargs)
