# -*- coding: future_fstrings -*-
import os
import logging
import sys
import yaml
import subprocess

from .logging_utils import ConsoleColors, SelectiveFormatter, ColorizingFormatter, MultiplexingHandler

CONFIG = os.environ["CONFIG"]
INPUT = os.environ["INPUT"]
OUTPUT = os.environ["OUTPUT"]
MSDIR = os.environ["MSDIR"]


class ConfigNamespace(object):
    """A config namespace maps a dict with attribute-like keys to a namespace with attributes."""
    def __init__(self, mapping):
        for name, value in mapping.items():
            name = name.replace("-", "_")
            setattr(self, name, value)
    def get(self, key, default=None):
        return getattr(self, key, default)

# load config into a config namespace, and config["parameters"] into a parameters namespace
with open(CONFIG, "r") as _std:
    config = ConfigNamespace(yaml.safe_load(_std))
    parameters = ConfigNamespace({p["name"]: p["value"] for p in getattr(config, 'parameters', [])})

def init_logger(name="STIMELA",
           fmt="{asctime}: {message}",
           col_fmt="{asctime}: {message}%s"%(ConsoleColors.BEGIN, ConsoleColors.END),
           datefmt="%Y-%m-%d %H:%M:%S", loglevel="INFO"):
    """Returns the global Stimela logger (initializing if not already done so, with the given values)"""
    global log
    if log is None:
        log = logging.getLogger(name)
        log.propagate = False
        log.setLevel(getattr(logging, config.get("loglevel", "INFO"), logging.INFO))

        global log_console_handler, log_formatter, log_boring_formatter, log_colourful_formatter

        # this function checks if the log record corresponds to stdout/stderr output from a cab
        def _is_from_subprocess(rec):
            return hasattr(rec, 'stimela_subprocess_output')

        log_boring_formatter = logging.Formatter(fmt, datefmt, style="{")

        log_colourful_formatter = ColorizingFormatter(col_fmt, datefmt, style="{")

        log_formatter = log_colourful_formatter

        log_console_handler = MultiplexingHandler()
        log_console_handler.setFormatter(log_formatter)
        log_console_handler.setLevel(getattr(logging, loglevel))
        log.addHandler(log_console_handler)
    return log

log = None

if log is None:
    log = init_logger()

def report_memory():
    """Reports memory status"""
    try:
        output = subprocess.check_output(["/usr/bin/free", "-h"]).decode().splitlines(keepends=False)
    except subprocess.CalledProcessError as exc:
        log.warning(f"/usr/bin/free -h exited with code {exc.returncode}")
        return
    for line in output:
        log.info(line)

log.info("Initial memory state:")
report_memory()

from .proc_utils import prun, prun_multi, clear_junk