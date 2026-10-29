import threading
import time

import pytest

from jobscout.control import RunCancelled, RunControl


def test_checkpoint_blocks_while_paused_then_resumes() -> None:
    control = RunControl()
    passed = threading.Event()

    control.pause()

    def worker() -> None:
        control.checkpoint("test")
        passed.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert not passed.wait(0.15)

    control.resume()
    assert passed.wait(1.0)
    thread.join(1.0)
    assert not thread.is_alive()


def test_cancel_raises_at_checkpoint() -> None:
    control = RunControl()
    control.cancel()
    with pytest.raises(RunCancelled):
        control.checkpoint("test")


def test_sleep_stops_when_cancelled() -> None:
    control = RunControl()
    cancelled = threading.Event()

    def worker() -> None:
        try:
            control.sleep(5.0)
        except RunCancelled:
            cancelled.set()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.05)
    control.cancel()
    assert cancelled.wait(1.0)
    thread.join(1.0)
    assert not thread.is_alive()
