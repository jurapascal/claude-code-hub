"""
The one place where the platforms actually differ: opening a pseudo-terminal.

POSIX gets it from the stdlib (`pty`), Windows from ConPTY through pywinpty.
Both expose the same tiny interface, so the server above never branches on OS:

    p = spawn(argv, cwd, env, cols, rows)
    p.read()            -> bytes (b"" means the child is gone)
    p.write(b"...")
    p.resize(cols, rows)
    p.alive()           -> bool
    p.close()
"""
import codecs
import os
import subprocess
import time

IS_WINDOWS = os.name == "nt"

# How long a reader loop naps when the backend has nothing to hand us. Only the
# Windows path can return early with no data; POSIX blocks in os.read().
_IDLE = 0.01


class PtyUnavailable(RuntimeError):
    """Raised when this machine cannot open a pty (Windows without pywinpty)."""


class _PosixPty:
    def __init__(self, argv, cwd, env, cols, rows):
        import pty
        self.master, slave = pty.openpty()
        self._resize_fd(cols, rows)  # set before spawn so the child starts right
        try:
            self.proc = subprocess.Popen(
                argv, cwd=cwd, env=env,
                stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, close_fds=True)
        finally:
            os.close(slave)

    def _resize_fd(self, cols, rows):
        import fcntl
        import struct
        import termios
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    def read(self):
        try:
            return os.read(self.master, 65536)
        except OSError:
            return b""  # EIO — the child closed the other end
        except ValueError:
            return b""  # fd already closed by close()

    def write(self, data):
        """Write every byte, or none of the rest.

        os.write on a pty master is a short write as soon as the line discipline
        buffer fills up, which a paste easily does. Returning after one call
        would drop the tail — and if the cut lands inside a multi-byte character,
        what survives is half of an accented letter.
        """
        view = memoryview(data)
        while view:
            try:
                n = os.write(self.master, view)
            except BlockingIOError:
                time.sleep(_IDLE)
                continue
            except OSError:
                return
            if n <= 0:
                return
            view = view[n:]

    def resize(self, cols, rows):
        self._resize_fd(cols, rows)

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        # Kill the whole process group: the tab runs `bash -c '... ; exec bash'`,
        # so there can be children hanging off it.
        try:
            import signal
            os.killpg(os.getpgid(self.proc.pid), signal.SIGHUP)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            os.close(self.master)
        except Exception:
            pass


class _WindowsPty:
    def __init__(self, argv, cwd, env, cols, rows):
        try:
            from winpty import PtyProcess
        except ImportError as exc:  # pragma: no cover — depends on the machine
            raise PtyUnavailable(
                "pywinpty chybí — nainstaluj: pip install pywinpty") from exc
        self.proc = PtyProcess.spawn(
            argv, cwd=cwd, env={str(k): str(v) for k, v in env.items()},
            dimensions=(rows, cols))
        self._closed = False
        # pywinpty hands us str, so what we write has to be str too — and a
        # websocket frame can end mid-character. Decoding incrementally keeps
        # the two halves of an accented letter together instead of turning the
        # first one into U+FFFD.
        self._in = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self):
        """Block until there is output, or b'' once the child is gone.

        pywinpty returns '' both for "nothing yet" and for EOF, so the loop —
        not the caller — is what tells them apart.
        """
        while not self._closed:
            try:
                data = self.proc.read(65536)
            except EOFError:
                return b""
            except Exception:
                return b""
            if data:
                return data.encode("utf-8", "replace") if isinstance(data, str) else data
            if not self.alive():
                return b""
            time.sleep(_IDLE)
        return b""

    def write(self, data):
        try:
            text = self._in.decode(data)
            if text:
                self.proc.write(text)
        except Exception:
            pass

    def resize(self, cols, rows):
        try:
            self.proc.setwinsize(rows, cols)
        except Exception:
            pass

    def alive(self):
        try:
            return bool(self.proc.isalive())
        except Exception:
            return False

    def close(self):
        self._closed = True  # wakes the read loop within _IDLE
        try:
            self.proc.terminate(force=True)
        except Exception:
            pass


def spawn(argv, cwd=None, env=None, cols=80, rows=24):
    """Start argv under a pty. Raises PtyUnavailable if the OS can't provide one."""
    env = env or dict(os.environ)
    if cwd and not os.path.isdir(cwd):
        cwd = None
    if IS_WINDOWS:
        return _WindowsPty(argv, cwd, env, cols, rows)
    return _PosixPty(argv, cwd, env, cols, rows)


def selftest():  # pragma: no cover — used by install scripts and `--doctor`
    """Round-trip a tiny command through a pty; returns (ok, detail)."""
    try:
        argv = ["cmd", "/c", "echo hub-ok"] if IS_WINDOWS else ["/bin/sh", "-c", "echo hub-ok"]
        p = spawn(argv, cols=80, rows=24)
        out = b""
        deadline = time.time() + 5
        while time.time() < deadline:
            chunk = p.read()
            if chunk:
                out += chunk
            elif not p.alive():
                break
        p.close()
        text = out.decode("utf-8", "replace")
        return ("hub-ok" in text), text.strip()
    except Exception as exc:
        return False, str(exc)
