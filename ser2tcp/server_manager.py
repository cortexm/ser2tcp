"""Server manager"""

import selectors as _selectors


class ServersManager():
    """Servers manager - one selectors loop driving every subsystem.

    Servers, their connections, the serial ports and uhttp all register
    their own sockets in this selector and carry themselves as the key's
    data, so dispatch is a single call to the owner rather than a fan-out
    over every socket in the process.
    """

    TIMEOUT = .1

    def __init__(self, selector=None):
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
            self._dispatch(key, mask)
        for server in self._servers:
            server.process_stale()

    def _dispatch(self, key, mask):
        """Hand one ready socket to whatever owns it.

        Handling an event can close sockets other than its own — a serial
        port failing drops every client on that port — so by the time we
        reach a later event from the same batch its owner may be gone.
        Each handle_event() is responsible for recognising that.
        """
        ready = key.data.handle_event(key.fileobj, mask)
        while ready is not None:
            if self._client_handler is not None:
                self._client_handler(ready)
            # One recv() can carry several requests or WebSocket frames,
            # and select() will not report them again - the OS buffer is
            # already drained. Take them now.
            ready = ready if ready.next() else None

    def close(self):
        """Close all servers"""
        for server in self._servers:
            server.close()
        if self._owns_selector:
            self._selector.close()
