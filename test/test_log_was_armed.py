'''
Tests the ULog class
'''

import os
import inspect
import unittest

from ddt import ddt, data
from pyulog.utils import check_if_log_was_armed

TEST_PATH = os.path.dirname(os.path.abspath(
    inspect.getfile(inspect.currentframe())))


@ddt
class TestLogWasArmed(unittest.TestCase):
    '''
    Tests the check_if_log_was_armed function
    '''

    @data('sample_armed_flight')
    def test_log_was_armed(self, base_name):
        '''
        Test that the armed log is marked as armed.
        '''
        ulog_file_name = os.path.join(TEST_PATH, base_name + '.ulg')
        assert check_if_log_was_armed(ulog_file_name)

    @data('sample_not_armed_flight')
    def test_log_was_not_armed(self, base_name):
        '''
        Test that the non-armed log is marked as not armed.
        '''
        ulog_file_name = os.path.join(TEST_PATH, base_name + '.ulg')
        assert not check_if_log_was_armed(ulog_file_name)
