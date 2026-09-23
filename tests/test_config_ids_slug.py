"""Tests for the id an entry gets when nobody chose one.

An id travels in a URL, names an entry in config.json and is what
every link and script says. It used to be four random bytes, which
points at an entry without saying anything about it. It is derived
from the name now, so `esp32` says what `7f3a91c4` only pointed at.

SLUG_CASES is the contract, and app.js implements the same rule for
the editor - it has to *show* the id before the save that would
create it. The two are pinned to this table from both sides; when
they drift, one of them fails here.
"""

import unittest

from ser2tcp.config_ids import (
    FALLBACK_SLUG, HTTP_FALLBACK_SLUG, SLUG_MAX, assign_ids, entry_slug,
    fresh_id, is_valid_id, slug, unique_id)

# name -> id. Kept as data so the same cases can be run against the
# browser's copy of the rule.
SLUG_CASES = [
    ('esp32', 'esp32'),
    ('My Port', 'my-port'),
    ('My Port ##2', 'my-port-2'),
    ('my/port--2', 'my-port-2'),
    ('ttyUSB0', 'ttyusb0'),
    ('  spaced  out  ', 'spaced-out'),
    ('-leading-and-trailing-', 'leading-and-trailing'),
    ('ALL CAPS', 'all-caps'),
    ('dots.and_underscores', 'dots-and-underscores'),
    ('non-ascii: ěščřž', 'non-ascii'),
    ('###', ''),
    ('', ''),
]


class TestTheSlug(unittest.TestCase):

    def test_the_table(self):
        for name, expected in SLUG_CASES:
            self.assertEqual(slug(name), expected, name)

    def test_two_names_can_mean_one_id(self):
        """Which is why a collision counts ids, not names."""
        self.assertEqual(slug('My Port'), slug('my.port'))

    def test_it_is_always_a_valid_id(self):
        for name, expected in SLUG_CASES:
            if expected:
                self.assertTrue(is_valid_id(expected), name)

    def test_a_long_name_is_cut(self):
        self.assertEqual(len(slug('x' * 200)), SLUG_MAX)

    def test_and_never_cut_onto_a_hyphen(self):
        """A name whose 48th character is a separator would otherwise
        end on one, which is the shape the rule forbids."""
        name = 'x' * (SLUG_MAX - 1) + ' tail'
        self.assertFalse(slug(name).endswith('-'))

    def test_something_that_is_not_a_string(self):
        for value in (None, 12, [], {}):
            self.assertEqual(slug(value), '')


class TestWhatItIsDerivedFrom(unittest.TestCase):

    def test_the_name(self):
        self.assertEqual(entry_slug({'name': 'My Port'}), 'my-port')

    def test_the_device_when_there_is_no_name(self):
        """An entry added over the API need not carry a name, and the
        device is the other thing a person would recognise it by."""
        self.assertEqual(
            entry_slug({'serial': {'port': '/dev/ttyUSB0'}}), 'ttyusb0')

    def test_and_a_name_that_slugs_to_nothing_falls_through_too(self):
        self.assertEqual(
            entry_slug({'name': '###', 'serial': {'port': '/dev/ttyS3'}}),
            'ttys3')

    def test_a_fallback_when_there_is_neither(self):
        self.assertEqual(entry_slug({}), FALLBACK_SLUG)

    def test_a_port_found_by_usb_match_has_no_device_to_use(self):
        self.assertEqual(
            entry_slug({'serial': {'match': {'vid': '0x303A'}}}),
            FALLBACK_SLUG)


class TestWhenTheIdIsTaken(unittest.TestCase):

    def test_the_first_one_is_the_name_itself(self):
        self.assertEqual(unique_id('esp32', set()), 'esp32')

    def test_the_next_is_numbered(self):
        self.assertEqual(unique_id('esp32', {'esp32'}), 'esp32-1')

    def test_and_counts_up_past_what_is_there(self):
        self.assertEqual(
            unique_id('esp32', {'esp32', 'esp32-1', 'esp32-2'}), 'esp32-3')

    def test_a_gap_is_filled_rather_than_skipped(self):
        self.assertEqual(unique_id('esp32', {'esp32', 'esp32-2'}), 'esp32-1')

    def test_an_empty_base_still_gives_something(self):
        self.assertEqual(unique_id('', set()), FALLBACK_SLUG)


class TestFillingThemIn(unittest.TestCase):

    def test_a_port_is_named_after_itself(self):
        config = {'ports': [{'name': 'ESP32 dev'}]}
        self.assertTrue(assign_ids(config))
        self.assertEqual(config['ports'][0]['id'], 'esp32-dev')

    def test_two_of_the_same_name_are_told_apart(self):
        config = {'ports': [{'name': 'esp32'}, {'name': 'esp32'}]}
        assign_ids(config)
        self.assertEqual(
            [p['id'] for p in config['ports']], ['esp32', 'esp32-1'])

    def test_an_id_already_written_down_is_left_alone(self):
        """Whatever it looks like, it is what links and scripts are
        using - including the random ones handed out before this."""
        config = {'ports': [{'name': 'esp32', 'id': '7f3a91c4'}]}
        self.assertFalse(assign_ids(config))
        self.assertEqual(config['ports'][0]['id'], '7f3a91c4')

    def test_and_is_not_walked_over_by_a_new_one(self):
        config = {'ports': [{'name': 'esp32', 'id': 'esp32'},
                            {'name': 'esp32'}]}
        assign_ids(config)
        self.assertEqual(config['ports'][1]['id'], 'esp32-1')

    def test_ports_and_http_servers_share_the_space(self):
        """The URL says which kind is meant, but an id that means two
        things is confusing whatever the URL says."""
        config = {'ports': [{'name': 'main'}], 'http': [{'name': 'main'}]}
        assign_ids(config)
        self.assertEqual(config['ports'][0]['id'], 'main')
        self.assertEqual(config['http'][0]['id'], 'main-1')

    def test_an_http_server_with_nothing_to_go_on_is_not_called_port(self):
        """Its kind is the last thing left to say about it."""
        config = {'http': [{'address': '0.0.0.0', 'port': 8080}]}
        assign_ids(config)
        self.assertEqual(config['http'][0]['id'], HTTP_FALLBACK_SLUG)

    def test_and_a_port_with_nothing_to_go_on_is(self):
        config = {'ports': [{'serial': {'match': {'vid': '0x1'}}}]}
        assign_ids(config)
        self.assertEqual(config['ports'][0]['id'], FALLBACK_SLUG)

    def test_a_duplicate_is_replaced(self):
        config = {'ports': [{'name': 'a', 'id': 'same'},
                            {'name': 'b', 'id': 'same'}]}
        assign_ids(config)
        ids = [p['id'] for p in config['ports']]
        self.assertEqual(len(set(ids)), 2)


class TestAskingForOne(unittest.TestCase):

    def test_it_avoids_what_is_in_the_configuration(self):
        config = {'ports': [{'id': 'esp32'}]}
        self.assertEqual(fresh_id(config, {'name': 'esp32'}), 'esp32-1')

    def test_without_an_entry_it_still_answers(self):
        self.assertEqual(fresh_id({'ports': []}), FALLBACK_SLUG)


if __name__ == '__main__':
    unittest.main()
