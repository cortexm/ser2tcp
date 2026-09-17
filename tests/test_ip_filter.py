"""Tests for IP filter"""

import unittest

from ser2tcp.ip_filter import IpFilter, create_filter


class TestIpFilter(unittest.TestCase):
    """Test IpFilter class"""

    def test_no_rules_allows_all(self):
        """No rules should allow all IPs"""
        flt = IpFilter()
        self.assertTrue(flt.is_allowed('192.168.1.1'))
        self.assertTrue(flt.is_allowed('10.0.0.1'))
        self.assertTrue(flt.is_allowed('8.8.8.8'))

    def test_allow_single_ip(self):
        """Allow list with single IP"""
        flt = IpFilter(allow=['192.168.1.100'])
        self.assertTrue(flt.is_allowed('192.168.1.100'))
        self.assertFalse(flt.is_allowed('192.168.1.101'))
        self.assertFalse(flt.is_allowed('10.0.0.1'))

    def test_allow_cidr(self):
        """Allow list with CIDR notation"""
        flt = IpFilter(allow=['192.168.1.0/24'])
        self.assertTrue(flt.is_allowed('192.168.1.1'))
        self.assertTrue(flt.is_allowed('192.168.1.254'))
        self.assertFalse(flt.is_allowed('192.168.2.1'))
        self.assertFalse(flt.is_allowed('10.0.0.1'))

    def test_allow_multiple(self):
        """Allow list with multiple entries"""
        flt = IpFilter(allow=['192.168.1.0/24', '10.0.0.5'])
        self.assertTrue(flt.is_allowed('192.168.1.50'))
        self.assertTrue(flt.is_allowed('10.0.0.5'))
        self.assertFalse(flt.is_allowed('10.0.0.6'))

    def test_deny_single_ip(self):
        """Deny list with single IP"""
        flt = IpFilter(deny=['192.168.1.100'])
        self.assertFalse(flt.is_allowed('192.168.1.100'))
        self.assertTrue(flt.is_allowed('192.168.1.101'))
        self.assertTrue(flt.is_allowed('10.0.0.1'))

    def test_deny_cidr(self):
        """Deny list with CIDR notation"""
        flt = IpFilter(deny=['10.0.0.0/8'])
        self.assertFalse(flt.is_allowed('10.0.0.1'))
        self.assertFalse(flt.is_allowed('10.255.255.255'))
        self.assertTrue(flt.is_allowed('192.168.1.1'))

    def test_deny_takes_precedence(self):
        """Deny should take precedence over allow"""
        flt = IpFilter(
            allow=['192.168.1.0/24'],
            deny=['192.168.1.100'])
        self.assertTrue(flt.is_allowed('192.168.1.1'))
        self.assertTrue(flt.is_allowed('192.168.1.99'))
        self.assertFalse(flt.is_allowed('192.168.1.100'))
        self.assertTrue(flt.is_allowed('192.168.1.101'))

    def test_deny_without_allow(self):
        """Deny only - all allowed except denied"""
        flt = IpFilter(deny=['10.0.0.0/8'])
        self.assertTrue(flt.is_allowed('192.168.1.1'))
        self.assertTrue(flt.is_allowed('8.8.8.8'))
        self.assertFalse(flt.is_allowed('10.1.2.3'))

    def test_invalid_ip(self):
        """Invalid IP address should be denied"""
        flt = IpFilter(allow=['192.168.1.0/24'])
        self.assertFalse(flt.is_allowed('invalid'))
        self.assertFalse(flt.is_allowed(''))
        self.assertFalse(flt.is_allowed('256.1.1.1'))

    def test_ipv6(self):
        """IPv6 addresses"""
        flt = IpFilter(allow=['::1', 'fe80::/10'])
        self.assertTrue(flt.is_allowed('::1'))
        self.assertTrue(flt.is_allowed('fe80::1'))
        self.assertFalse(flt.is_allowed('2001:db8::1'))

    def test_is_enabled(self):
        """is_enabled property"""
        self.assertFalse(IpFilter().is_enabled)
        self.assertTrue(IpFilter(allow=['1.2.3.4']).is_enabled)
        self.assertTrue(IpFilter(deny=['1.2.3.4']).is_enabled)

    def test_invalid_network_in_config(self):
        """A rule that cannot be read is refused, not dropped.

        Dropping it left a filter that looked configured and enforced
        less than it said - and a typo in the one deny rule was the
        difference between blocked and allowed.
        """
        with self.assertRaises(ValueError):
            IpFilter(allow=['192.168.1.0/24', 'invalid', '10.0.0.0/8'])


class TestCreateFilter(unittest.TestCase):
    """Test create_filter function"""

    def test_no_config(self):
        """No allow/deny in config returns None"""
        self.assertIsNone(create_filter({}))
        self.assertIsNone(create_filter({'port': 8080}))

    def test_with_allow(self):
        """Config with allow list"""
        flt = create_filter({'allow': ['192.168.1.0/24']})
        self.assertIsNotNone(flt)
        self.assertTrue(flt.is_allowed('192.168.1.1'))

    def test_with_deny(self):
        """Config with deny list"""
        flt = create_filter({'deny': ['10.0.0.0/8']})
        self.assertIsNotNone(flt)
        self.assertFalse(flt.is_allowed('10.1.2.3'))

    def test_with_both(self):
        """Config with both allow and deny"""
        flt = create_filter({
            'allow': ['192.168.0.0/16'],
            'deny': ['192.168.1.100']
        })
        self.assertIsNotNone(flt)
        self.assertTrue(flt.is_allowed('192.168.2.1'))
        self.assertFalse(flt.is_allowed('192.168.1.100'))


class TestAnIpv4ClientOnADualStackSocket(unittest.TestCase):
    """The address a v4 client arrives with when the socket is v6.

    uhttp binds AF_INET6 whenever the address contains a colon ("::"),
    without IPV6_V6ONLY, so an IPv4 client shows up as
    ::ffff:192.168.1.100. Comparing that against an IPv4Network is
    always False, which is the wrong answer in both directions: deny
    rules stopped matching and allow rules stopped letting anyone in.
    """

    MAPPED = '::ffff:192.168.1.100'

    def test_a_deny_rule_reaches_the_mapped_form(self):
        flt = IpFilter(deny=['192.168.1.100'])
        self.assertFalse(flt.is_allowed(self.MAPPED))

    def test_a_deny_network_reaches_it_too(self):
        flt = IpFilter(deny=['192.168.1.0/24'])
        self.assertFalse(flt.is_allowed(self.MAPPED))

    def test_an_allow_rule_lets_the_mapped_form_in(self):
        flt = IpFilter(allow=['192.168.1.0/24'])
        self.assertTrue(flt.is_allowed(self.MAPPED))

    def test_someone_else_is_still_refused(self):
        flt = IpFilter(allow=['192.168.1.0/24'])
        self.assertFalse(flt.is_allowed('::ffff:10.0.0.1'))

    def test_a_real_ipv6_client_is_unaffected(self):
        flt = IpFilter(allow=['2001:db8::/32'])
        self.assertTrue(flt.is_allowed('2001:db8::1'))
        self.assertFalse(flt.is_allowed('2001:dead::1'))

    def test_a_rule_written_in_the_mapped_form_also_works(self):
        """The same mismatch, from the other side"""
        flt = IpFilter(deny=[self.MAPPED])
        self.assertFalse(flt.is_allowed('192.168.1.100'))
        self.assertFalse(flt.is_allowed(self.MAPPED))

    def test_a_mapped_network_rule_keeps_its_size(self):
        flt = IpFilter(allow=['::ffff:192.168.1.0/120'])
        self.assertTrue(flt.is_allowed('192.168.1.55'))
        self.assertFalse(flt.is_allowed('192.168.2.55'))


class TestARuleThatCannotBeReadIsRefused(unittest.TestCase):
    """A filter must never end up enforcing less than it says.

    An unreadable entry was logged and dropped, so a typo silently
    removed a rule. A plain string instead of a list was worse: it was
    iterated character by character, every character failed to parse,
    and what was left was a filter with no rules at all - which allows
    everyone.
    """

    def test_a_typo_in_a_deny_rule_raises(self):
        with self.assertRaises(ValueError):
            IpFilter(deny=['192.168.1.1OO'])

    def test_a_typo_in_an_allow_rule_raises(self):
        with self.assertRaises(ValueError):
            IpFilter(allow=['not an address'])

    def test_the_message_names_the_list_and_the_entry(self):
        with self.assertRaises(ValueError) as caught:
            IpFilter(deny=['nonsense'])
        self.assertIn('deny', str(caught.exception))
        self.assertIn('nonsense', str(caught.exception))

    def test_a_bare_string_is_not_a_list_of_rules(self):
        with self.assertRaises(ValueError):
            IpFilter(allow='10.0.0.5')

    def test_and_says_so_rather_than_naming_a_character(self):
        with self.assertRaises(ValueError) as caught:
            IpFilter(allow='10.0.0.5')
        self.assertIn('list', str(caught.exception))

    def test_an_entry_that_is_not_a_string_raises(self):
        with self.assertRaises(ValueError):
            IpFilter(allow=[3232235777])

    def test_none_still_means_no_rules(self):
        flt = IpFilter(allow=None, deny=None)
        self.assertFalse(flt.is_enabled)

    def test_an_empty_list_still_means_no_rules(self):
        flt = IpFilter(allow=[], deny=[])
        self.assertFalse(flt.is_enabled)

    def test_create_filter_passes_the_refusal_on(self):
        with self.assertRaises(ValueError):
            create_filter({'deny': ['nonsense']})


if __name__ == '__main__':
    unittest.main()
