"""
Module for handling extenal calls.
"""

import subprocess


class Error(Exception):
    """ Exception class for this module """
    def __init__(self, result):
        super(Error, self).__init__(
            '{} exited with status {}'.format(
                result.command, result.status)
        )
        self.result = result


# pylint: disable-msg=too-few-public-methods
class Result(object):
    """ Result of external call """
    def __init__(self, command, status, output, error):
        self.command = command
        self.status = status
        self.output = output
        self.error = error

    def assert_status(self, *allowed_status):
        """ Raise Error if staus not allowed """
        if self.status not in allowed_status:
            raise Error(self)
        return self


def call(*command):
    """ Call external application """
    pipe = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    stdout, stderr = pipe.communicate()
    return Result(command, pipe.returncode, stdout, stderr)
