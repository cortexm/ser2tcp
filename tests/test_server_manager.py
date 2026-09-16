"""Tests for ServersManager - the one event loop driving everything.

A bug in any handler used to reach main() and take the whole process
down, sockets, serial ports and all. These tests pin down that one
misbehaving owner costs only its own event.
"""

import selectors
import unittest
from unittest.mock import Mock

import ser2tcp.server_manager as _server_manager


def make_key(data, fileobj=None):
    """Build a SelectorKey the way selectors.select() hands them back"""
    if fileobj is None:
        fileobj = Mock()
    return selectors.SelectorKey(
        fileobj=fileobj, fd=1, events=selectors.EVENT_READ, data=data)


class FakeSelector():
    """Minimal selector: reports a fixed batch of ready keys once"""

    def __init__(self, events=()):
        self.events = list(events)
        self.closed = False

    def select(self, timeout=None):
        """Return the pending batch, then nothing"""
        events, self.events = self.events, []
        return [(key, key.events) for key in events]

    def close(self):
        """Record that the loop closed us"""
        self.closed = True


class TestDispatchErrors(unittest.TestCase):
    """An owner raising out of handle_event() must not stop the loop"""

    def setUp(self):
        self.log = Mock()

    def _manager(self, keys):
        selector = FakeSelector(keys)
        return _server_manager.ServersManager(
            selector=selector, log=self.log), selector

    def test_handler_exception_does_not_propagate(self):
        """A raising handler is contained inside process()"""
        owner = Mock()
        owner.handle_event.side_effect = AttributeError('boom')
        manager, _ = self._manager([make_key(owner)])
        manager.process()
        owner.handle_event.assert_called_once()

    def test_handler_exception_is_logged(self):
        """The failure is reported, not silently dropped"""
        owner = Mock()
        owner.handle_event.side_effect = AttributeError('boom')
        manager, _ = self._manager([make_key(owner)])
        manager.process()
        self.log.exception.assert_called_once()

    def test_later_events_in_the_same_batch_are_still_dispatched(self):
        """One broken owner does not cost the others their event"""
        broken = Mock()
        broken.handle_event.side_effect = RuntimeError('boom')
        healthy = Mock()
        healthy.handle_event.return_value = None
        manager, _ = self._manager([make_key(broken), make_key(healthy)])
        manager.process()
        healthy.handle_event.assert_called_once()

    def test_client_handler_exception_does_not_propagate(self):
        """The uhttp path (returned objects) is covered too"""
        ready = Mock()
        ready.next.return_value = False
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        handler = Mock(side_effect=AttributeError('boom'))
        manager.set_client_handler(handler)
        manager.process()
        handler.assert_called_once_with(ready)
        self.log.exception.assert_called_once()

    def test_a_failing_handler_closes_the_connection(self):
        """Containing the exception is not enough on its own.

        A handler that raises never answered, so uhttp still holds the
        request and reports the connection ready on every pass after
        this one - a 100% CPU spin that outlives the request, the
        client and any interest in the answer. Letting go of the
        connection is the only way out.
        """
        ready = Mock()
        ready.next.return_value = True
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        manager.set_client_handler(Mock(side_effect=AttributeError('boom')))
        manager.process()
        ready.close.assert_called_once()

    def test_a_failing_handler_tries_to_answer_first(self):
        """The client hears 500 rather than waiting out a timeout"""
        ready = Mock()
        ready.next.return_value = False
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        manager.set_client_handler(Mock(side_effect=AttributeError('boom')))
        manager.process()
        self.assertEqual(ready.respond.call_args.kwargs.get('status'), 500)

    def test_a_connection_that_failed_is_not_drained_further(self):
        """Nothing more is pulled out of a connection being dropped"""
        ready = Mock()
        ready.next.return_value = True
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        manager.set_client_handler(Mock(side_effect=AttributeError('boom')))
        manager.process()
        ready.next.assert_not_called()

    def test_a_failing_next_also_closes_the_connection(self):
        """Draining the rest of the buffer can fail the same way"""
        ready = Mock()
        ready.next.side_effect = RuntimeError('boom')
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        manager.set_client_handler(Mock())
        manager.process()
        ready.close.assert_called_once()

    def test_closing_a_broken_connection_survives_a_broken_close(self):
        """Both the answer and the close may fail on a dead socket"""
        ready = Mock()
        ready.next.return_value = False
        ready.respond.side_effect = OSError('gone')
        ready.close.side_effect = OSError('gone')
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        manager.set_client_handler(Mock(side_effect=AttributeError('boom')))
        manager.process()
        ready.close.assert_called_once()

    def test_a_healthy_connection_is_left_alone(self):
        """Nothing is closed when the handler does its job"""
        ready = Mock()
        ready.next.return_value = False
        owner = Mock()
        owner.handle_event.return_value = ready
        manager, _ = self._manager([make_key(owner)])
        manager.set_client_handler(Mock())
        manager.process()
        ready.close.assert_not_called()
        self.log.exception.assert_not_called()

    def test_keyboard_interrupt_is_not_swallowed(self):
        """Ctrl-C and SystemExit still reach the caller"""
        owner = Mock()
        owner.handle_event.side_effect = KeyboardInterrupt()
        manager, _ = self._manager([make_key(owner)])
        with self.assertRaises(KeyboardInterrupt):
            manager.process()

    def test_run_keeps_going_after_a_failing_handler(self):
        """The loop survives a failure and still reaches close()"""
        owner = Mock()
        calls = []

        def explode_then_stop(*_args):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError('boom')
            manager.stop()
            return None

        owner.handle_event.side_effect = explode_then_stop
        selector = FakeSelector()
        manager = _server_manager.ServersManager(
            selector=selector, log=self.log)
        selector.events = [make_key(owner)]

        original_select = selector.select

        def select_again(timeout=None):
            events = original_select(timeout)
            selector.events = [make_key(owner)]
            return events

        selector.select = select_again
        manager.run()
        self.assertEqual(len(calls), 2)


class TestProcessStaleErrors(unittest.TestCase):
    """Periodic work is as fatal as event handling if left unguarded"""

    def setUp(self):
        self.log = Mock()
        self.manager = _server_manager.ServersManager(
            selector=FakeSelector(), log=self.log)

    def test_process_stale_exception_does_not_propagate(self):
        """A server failing its periodic pass does not stop the loop"""
        broken = Mock()
        broken.process_stale.side_effect = RuntimeError('boom')
        self.manager.add_server(broken)
        self.manager.process()
        self.log.exception.assert_called_once()

    def test_other_servers_still_get_their_periodic_pass(self):
        """One broken server does not starve the rest"""
        broken = Mock()
        broken.process_stale.side_effect = RuntimeError('boom')
        healthy = Mock()
        self.manager.add_server(broken)
        self.manager.add_server(healthy)
        self.manager.process()
        healthy.process_stale.assert_called_once()

    def test_close_continues_after_a_failing_server(self):
        """Shutdown closes everything it can, then the selector"""
        broken = Mock()
        broken.close.side_effect = OSError('boom')
        healthy = Mock()
        self.manager.add_server(broken)
        self.manager.add_server(healthy)
        self.manager.close()
        healthy.close.assert_called_once()
        self.log.exception.assert_called_once()


if __name__ == '__main__':
    unittest.main()
