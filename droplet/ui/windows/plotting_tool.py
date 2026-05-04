"""PlottingToolWindow - publication-quality figure editor (~3000 lines).

The class is defined in droplet/app.py for now because it references
main_win and the current spectrum data at construction time.

TODO: migrate here once the main application state is encapsulated in a
proper class (e.g. MainApp) so dependencies can be injected cleanly.
"""


def PlottingToolWindow(*args, **kwargs):
    from droplet.app import PlottingToolWindow as _cls
    return _cls(*args, **kwargs)
