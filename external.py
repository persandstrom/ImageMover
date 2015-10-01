import subprocess

class Error(Exception):
    def __init__(self, result):
        super(Error, self).__init__(
            '{} exited with status {}'.format(
                result.command, result.status)
        )
        self.result = result

class Result(object):
    def __init__(self, command, status, output, error):
        self.command = command
        self.status = status
        self.output = output
        self.error = error

    def assert_status(self, *allowed_status):
        if not self.status in allowed_status:
            raise Error(self)
        return self

def call(*command):
    pipe = subprocess.Popen(list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = pipe.communicate()
    return Result(command, pipe.returncode, stdout, stderr)
