import errno
import os
import shlex
import socket
import tempfile
import types
from subprocess import Popen
from time import sleep

# uses Unix sockets for determine running process.
# (assumes used daemons will register proper socket)
# all daemons should use -p argument as listening tcp port
class SingletonDaemon(object):

    # run_cmd can be function of how to run daemon or a str to run at subprocess
    def __init__(self, name, tag, port, run_cmd, dir = None):
        self.name    = name
        self.tag     = tag
        self.port    = port
        self.run_cmd = run_cmd
        self.dir     = dir
        self.stop    = self.kill # alias
        if ' ' in tag:
            raise Exception('Error: tag should not include spaces')
        if dir and not os.path.exists(dir):
            print('Warning: path given for %s: %s, does not exist' % (name, dir))


    # returns True if daemon is running
    def is_running(self):
        lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            lock_socket.bind('\0' + self.tag) # the check is ~200000 faster and more reliable than checking via 'netstat' or 'ps' etc.
            lock_socket.close()
        except socket.error: # Unix socket in use
            return True
        # Unix socket is not used, but maybe it's old version of daemon not using socket
        return bool(self.get_pid())


    # get pid of running daemon by registered Unix socket (most robust way)
    def get_pid_by_unix_socket(self):
        ret_code, stdout, stderr = run_command('netstat -px')
        if ret_code:
            raise Exception('Error running netstat: %s' % [ret_code, stdout, stderr])
        for line in stdout.splitlines():
            line_arr = line.strip().split()
            if len(line_arr) == 8 and line_arr[0] == 'unix' and line_arr[4] == 'DGRAM' and line_arr[7] == '@%s' % self.tag:
                return int(line_arr[6].split('/', 1)[0])


    # get pid of running daemon by listening tcp port (for backward compatibility)
    def get_pid_by_listening_port(self):
        ret_code, stdout, stderr = run_command('netstat -tlnp')
        if ret_code:
            raise Exception('Error running netstat: %s' % [ret_code, stdout, stderr])
        for line in stdout.splitlines():
            line_arr = line.strip().split()
            if len(line_arr) == 7 and line_arr[3] == '0.0.0.0:%s' % self.port:
                if '/' not in line_arr[6]:
                    raise Exception('Expecting pid/program name in netstat line of using port %s, got: %s' % (self.port, line))
                return int(line_arr[6].split('/')[0])


    # get PID of running process, None if not found
    def get_pid(self):
        pid = self.get_pid_by_unix_socket()
        if pid:
            return pid
        pid = self.get_pid_by_listening_port()
        if pid:
            return pid


    # kill daemon
    def kill(self, timeout = 5):
        pid = self.get_pid()
        if not pid:
            return False
        ret_code, stdout, stderr = run_command('kill %s' % pid) # usual kill
        if ret_code:
            raise Exception('Failed to run kill command for %s: %s' % (self.name, [ret_code, stdout, stderr]))
        poll_rate = 0.1
        for i in range(int(timeout / poll_rate)):
            if not self.is_running():
                return True
            sleep(poll_rate)
        ret_code, stdout, stderr = run_command('kill -9 %s' % pid) # unconditional kill
        if ret_code:
            raise Exception('Failed to run kill -9 command for %s: %s' % (self.name, [ret_code, stdout, stderr]))
        poll_rate = 0.1
        for i in range(inr(timeout / poll_rate)):
            if not self.is_running():
                return True
            sleep(poll_rate)
        raise Exception('Could not kill %s, even with -9' % self.name)


    # start daemon
    # returns True if success, False if already running
    def start(self, timeout = 5):
        if self.is_running():
            raise Exception('%s is already running' % self.name)
        if not self.run_cmd:
            raise Exception('No starting command registered for %s' % self.name)
        if type(self.run_cmd) is types.FunctionType:
            self.run_cmd()
            return
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            proc = Popen(shlex.split('%s -p %s' % (self.run_cmd, self.port)), cwd = self.dir, close_fds = True,
                         stdout = stdout_file, stderr = stderr_file)
            if timeout > 0:
                poll_rate = 0.1
                for i in range(int(timeout/poll_rate)):
                    sleep(poll_rate)
                    if bool(proc.poll()): # process ended with error
                        stdout_file.seek(0)
                        stderr_file.seek(0)
                        raise Exception('Run of %s ended unexpectfully: %s' % (self.name, [proc.returncode, stdout_file.read().decode(errors = 'replace'), stderr_file.read().decode(errors = 'replace')]))
                    elif proc.poll() == 0: # process runs other process, and ended
                        break
            if self.is_running():
                return True
            raise Exception('%s failed to run.' % self.name)

    # restart the daemon
    def restart(self, timeout = 5):
        if self.is_running():
            self.kill(timeout)
        return self.start(timeout)


# provides unique way to determine running process, should be used inside daemon
def register_socket(tag):
    global lock_socket   # Without this our lock gets garbage collected
    lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        lock_socket.bind('\0%s' % tag)
    except socket.error:
        raise Exception('Error: process with tag %s is already running.' % tag)

# runs command
def run_command(command, timeout = 15, cwd = None):
    # pipes might stuck, even with timeout
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        proc = Popen(shlex.split(command), stdout = stdout_file, stderr = stderr_file, cwd = cwd, close_fds = True)
        if timeout > 0:
            poll_rate = 0.1
            for i in range(int(timeout/poll_rate)):
                sleep(poll_rate)
                if proc.poll() is not None: # process stopped
                    break
            if proc.poll() is None:
                proc.kill() # timeout
                return (errno.ETIME, '', 'Timeout on running: %s' % command)
        stdout_file.seek(0)
        stderr_file.seek(0)
        return (proc.returncode, stdout_file.read().decode(errors = 'replace'), stderr_file.read().decode(errors = 'replace'))
