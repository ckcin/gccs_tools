"""
A series of simple helper functions/modules for logging and error handling.
"""
import sys
import os
import datetime
import signal

# =============================================================================
# LOGGING ENGINE & SIGNAL HANDLING
# =============================================================================
class Logger:
    def __init__(self, level="INFO", use_colors=None, raise_on_error=False):
        self.levels = {
            "DEBUG": 0, "VERBOSE": 1, "INFO": 2,
            "QUIET": 3, "WARN": 3, "ERROR": 4
        }
        self.current_level = self.levels.get(level.upper(), 2)
        self.raise_on_error = raise_on_error

        if use_colors is not None:
            has_colors = use_colors
        else:
            is_a_tty = sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False
            has_no_color_env = "NO_COLOR" in os.environ
            has_colors = is_a_tty and not has_no_color_env

        if has_colors:
            self.colors = {
                "DEBUG": "\033[94m", "VERBOSE": "\033[36m", "INFO": "\033[92m",
                "WARN": "\033[93m", "ERROR": "\033[91m", "RESET": "\033[0m"
            }
        else:
            self.colors = {
                "DEBUG": "", "VERBOSE": "", "INFO": "",
                "WARN": "", "ERROR": "", "RESET": ""
            }

    def _msg(self, level, text):
        if self.levels.get(level, 2) >= self.current_level:
            ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            display_level = "WARN" if level == "QUIET" else level
            print(f"{ts} {self.colors.get(display_level, '')}[{display_level:<7}]{self.colors['RESET']} {text}", flush=True)

    def debug(self, text):   self._msg("DEBUG", text)
    def verbose(self, text): self._msg("VERBOSE", text)
    def info(self, text):    self._msg("INFO", text)
    def warn(self, text):    self._msg("WARN", text)
    def error(self, text):
        self._msg("ERROR", text)
        if self.raise_on_error:
            raise RuntimeError(text)
        else:
            sys.exit(1)


def setup_interrupt_handler(logger=None):
    """Configures the application to exit cleanly on Ctrl+C."""
    def signal_handler(sig, frame):
        msg = "\n[INTERRUPT] Execution halted by user. Cleaning up and exiting..."
        if logger:
            logger.warn(msg)
        else:
            print(msg)
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
