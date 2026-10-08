"""A cancelled poll must not outlive the storage engine it is using."""
import asyncio
import threading

from vedrfolnir.api import _poll_tick


def test_cancelled_poller_drains_background_thread():
    started = threading.Event()
    release = threading.Event()

    class BlockingService:
        def tick(self):
            started.set()
            assert release.wait(5), "test did not release the worker"

    async def exercise():
        task = asyncio.create_task(_poll_tick(BlockingService()))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "cancellation must wait for the running tick"
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("the awaiting poll must be cancelled")
        assert task.done()

    try:
        asyncio.run(exercise())
    finally:
        release.set()
