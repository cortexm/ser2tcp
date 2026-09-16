"""Server manager"""

import logging as _logging
import selectors as _selectors


class ServersManager():
    """Servers manager - one selectors loop driving every subsystem.

    Servers, their connections, the serial ports and uhttp all register
    their own sockets in this selector and carry themselves as the key's
    data, so dispatch is a single call to the owner rather than a fan-out
    over every socket in the process.

    Every call out of this loop is guarded: one loop serves every serial
    port, every client and the whole HTTP API, so an unhandled exception
    anywhere used to reach main() and take all of them down together.
    A failing owner now loses its own event and nothing else.
    """

    TIMEOUT = .1

    def __init__(self, selector=None, log=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._selector = selector or _selectors.DefaultSelector()
        self._owns_selector = selector is None
        self._servers = []
        self._running = False
        self._client_handler = None

    @property
    def selector(self):
        """The selector every server registers its sockets in"""
        return self._selector

    def set_client_handler(self, handler):
        """Set the callback for objects handle_event() hands back.

        uhttp owns its connections, so a ready request leaves the loop as
        a return value instead of arriving through a server we know about.
        HttpServerWrapper claims them here.
        """
        self._client_handler = handler

    def stop(self, _signo=None, _stack_frame=None):
        """Stop the server manager loop"""
        self._running = False

    def run(self):
        """Run the server manager loop"""
        self._running = True
        while self._running:
            self.process()
        self.close()

    def add_server(self, server):
        """Add server"""
        self._servers.append(server)

    def remove_server(self, server):
        """Remove server"""
        self._servers.remove(server)

    def process(self):
        """Wait for activity, dispatch it, then run periodic work"""
        try:
            events = self._selector.select(self.TIMEOUT)
        except OSError:
            # A socket closed underneath the selector; the owners reap
            # themselves on the next pass.
            events = ()
        for key, mask in events:
            try:
                self._dispatch(key, mask)
            except Exception:  # pylint: disable=W0703
                # Contain the damage to this one event. BaseException
                # (KeyboardInterrupt, SystemExit) still gets through.
                self._log.exception(
                    "Unhandled error handling event for %s",
                    type(key.data).__name__)
        for server in self._servers:
            try:
                server.process_stale()
            except Exception:  # pylint: disable=W0703
                self._log.exception(
                    "Unhandled error in %s.process_stale()",
                    type(server).__name__)

    def _dispatch(self, key, mask):
        """Hand one ready socket to whatever owns it.

        Handling an event can close sockets other than its own — a serial
        port failing drops every client on that port — so by the time we
        reach a later event from the same batch its owner may be gone.
        Each handle_event() is responsible for recognising that.
        """
        ready = key.data.handle_event(key.fileobj, mask)
        while ready is not None:
            try:
                if self._client_handler is not None:
                    self._client_handler(ready)
                # One recv() can carry several requests or WebSocket
                # frames, and select() will not report them again - the
                # OS buffer is already drained. Take them now.
                keep_going = ready.next()
            except Exception:  # pylint: disable=W0703
                self._log.exception("Unhandled error handling a request")
                self._drop_client(ready)
                return
            ready = ready if keep_going else None

    def _drop_client(self, client):
        """Let go of a connection whose handler failed.

        Containing the exception is not enough: a handler that raised
        never answered, so uhttp still holds the request and reports the
        connection ready on every pass from here on. That is a 100% CPU
        spin which outlives the request, the client, and any interest in
        the answer - the process has to be killed to stop it.

        Answering 500 first turns a silent hang into an error the client
        can act on; both steps are best-effort, because the reason the
        handler blew up may well be that the socket is already gone.
        """
        try:
            client.respond({'error': 'Internal server error'}, status=500)
        except Exception:  # pylint: disable=W0703
            pass
        try:
            client.close()
        except Exception:  # pylint: disable=W0703
            pass

    def close(self):
        """Close all servers"""
        for server in self._servers:
            try:
                server.close()
            except Exception:  # pylint: disable=W0703
                # Shutdown closes everything it can reach; one server
                # failing must not leave the rest open.
                self._log.exception(
                    "Unhandled error closing %s", type(server).__name__)
        if self._owns_selector:
            self._selector.close()
