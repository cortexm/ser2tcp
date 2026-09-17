"""IP address filtering (allow/deny lists with CIDR support)"""

import ipaddress as _ipaddress
import logging as _logging


# An IPv4 address inside the IPv6 space occupies the last 32 bits, so a
# v4-mapped prefix is the v6 prefix length minus these.
_MAPPED_PREFIX_BITS = 96


def _unmap_address(ip_addr):
    """An IPv4-mapped IPv6 address as the IPv4 address it carries.

    A socket bound to an address containing a colon ("::") is AF_INET6,
    and uhttp does not set IPV6_V6ONLY, so an IPv4 client arrives as
    ::ffff:192.168.1.100. That is never in an IPv4Network, which made
    every rule written the ordinary way miss it - deny rules stopped
    denying, and allow rules stopped allowing.
    """
    if isinstance(ip_addr, _ipaddress.IPv6Address) \
            and ip_addr.ipv4_mapped is not None:
        return ip_addr.ipv4_mapped
    return ip_addr


def _unmap_network(network):
    """The same for a rule, so both sides are compared in one family.

    Nobody is likely to write ::ffff:192.168.1.0/120 by hand, but the
    mismatch it would cause is the same one, in the other direction.
    """
    if isinstance(network, _ipaddress.IPv6Network) \
            and network.prefixlen >= _MAPPED_PREFIX_BITS:
        mapped = network.network_address.ipv4_mapped
        if mapped is not None:
            return _ipaddress.ip_network(
                f'{mapped}/{network.prefixlen - _MAPPED_PREFIX_BITS}',
                strict=False)
    return network


def _parse_rules(entries, where):
    """Parse one allow/deny list. Raises ValueError on anything unusable.

    Dropping an entry that cannot be read leaves a filter that looks
    configured and enforces less than it says: a typo in the one deny
    rule is the difference between blocked and allowed, and it only
    shows up as a warning nobody reads. A string where a list belongs
    was worse - it was iterated character by character, every character
    failed, and what remained was a filter with no rules at all, which
    allows everyone.
    """
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError(
            f"{where} must be a list of addresses, "
            f"got {type(entries).__name__}")
    networks = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(
                f"{where} entries must be strings, "
                f"got {type(entry).__name__}")
        try:
            network = _ipaddress.ip_network(entry, strict=False)
        except ValueError as err:
            raise ValueError(f"{where}: invalid '{entry}': {err}") from err
        networks.append(_unmap_network(network))
    return networks


class IpFilter:
    """Filter IP addresses against allow/deny lists.

    Logic:
    1. If IP matches deny list -> reject
    2. If allow list is empty -> allow (unless denied)
    3. If allow list is not empty -> IP must be in it

    Raises ValueError if any rule cannot be read. Refusing to build is
    the point: the caller turns that into a server that does not start
    and says why, which is the only safe direction for a filter to fail.
    """

    def __init__(self, allow=None, deny=None, log=None):
        self._log = log if log else _logging.getLogger(__name__)
        self._allow = _parse_rules(allow, 'allow')
        self._deny = _parse_rules(deny, 'deny')

    @property
    def is_enabled(self):
        """Return True if filter has any rules"""
        return bool(self._allow or self._deny)

    def is_allowed(self, ip_str):
        """Return True if IP address is allowed.

        Args:
            ip_str: IP address as string (e.g. "192.168.1.100")

        Returns:
            True if allowed, False if denied
        """
        try:
            ip_addr = _ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        ip_addr = _unmap_address(ip_addr)
        # Check deny list first
        for network in self._deny:
            if ip_addr in network:
                return False
        # If allow list is empty, allow (not denied)
        if not self._allow:
            return True
        # Check allow list
        for network in self._allow:
            if ip_addr in network:
                return True
        return False


def create_filter(config, log=None):
    """Create IpFilter from server config.

    Args:
        config: Server config dict with optional 'allow' and 'deny' lists
        log: Logger instance

    Returns:
        IpFilter instance or None if no filtering configured
    """
    allow = config.get('allow')
    deny = config.get('deny')
    if not allow and not deny:
        return None
    return IpFilter(allow=allow, deny=deny, log=log)
