import asyncio
import enum

from blelocate.scanner import ScannerRunner, explain_scanner_error
from fakes import TARGET, BackendFactory, wait_for


class Reason(enum.Enum):
    POWERED_OFF = 1


class NotAvailable(Exception):
    reason = Reason.POWERED_OFF


def test_runner_restarts_after_error_and_passes_callbacks_through():
    seen = []

    async def scenario():
        factory = BackendFactory(failures=[NotAvailable("Bluetooth radio is not powered on.")])
        runner = ScannerRunner(lambda *args: seen.append(args), backend_factory=factory, retry_delay_s=0.05)
        states = []
        task = asyncio.create_task(runner.run())
        await wait_for(lambda: runner.state == "stopped", message="first failure")
        states.append((runner.state, runner.error))
        await wait_for(lambda: runner.state == "running", message="restart")
        factory.current.emit("AA", [], "T", -61, service_data={TARGET: b"\x01"}, companies=[0x004C])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return states, runner, factory

    states, runner, factory = asyncio.run(scenario())
    assert states == [("stopped", "Bluetooth is turned off. Turn it on in Settings > Bluetooth & devices.")]
    assert runner.error is None and runner.state == "stopped"  # cancelled at the end
    assert seen == [("AA", [TARGET], "T", -61, [0x004C])]
    assert all(not b.running for b in factory.created)


def test_runner_restarts_scanner_after_silence():
    async def scenario():
        factory = BackendFactory()
        runner = ScannerRunner(lambda *a: None, backend_factory=factory, silence_restart_s=0.1)
        task = asyncio.create_task(runner.run())
        await wait_for(lambda: len(factory.created) >= 2, message="silent restart")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return runner

    runner = asyncio.run(scenario())
    assert "restarted the scanner" in runner.detail


def test_error_messages_tell_the_user_what_to_do():
    assert "setup.bat" in explain_scanner_error(ImportError("No module named 'winrt._winrt'"))
    err = OSError("The device is not ready for use")
    err.winerror = -2147020577
    assert "not ready" in explain_scanner_error(err)
    assert explain_scanner_error(RuntimeError("boom")) == "RuntimeError: boom"
