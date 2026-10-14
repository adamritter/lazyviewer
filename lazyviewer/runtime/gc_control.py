"""Keep cyclic garbage collection out of interactive rendering/input bursts."""
from contextlib import contextmanager
import gc


@contextmanager
def defer_cyclic_gc():
    """Reference counting continues normally; the event loop collects when idle."""
    enabled = gc.isenabled()
    if enabled:
        gc.disable()
    try:
        yield enabled
    finally:
        if enabled:
            gc.enable()
