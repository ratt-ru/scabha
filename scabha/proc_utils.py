# -*- coding: future_fstrings -*-
import subprocess
import shlex
import glob
import os.path
import shutil

from . import log, OUTPUT, MSDIR, config

def convert_command(command):
    """Converts list or str command into a string and a list"""
    if type(command) is str:
        command_list = shlex.split(command)
    elif type(command) is list:
        command_list = command
        command = ' '.join(command)
    else:
        raise TypeError("command: list or string expected")
    return command, command_list


def prun(command):
    """
    Runs a single command given by a string, or a list (strings will be split into lists by whitespace).
    Calls clear_junk() afterwards.

    Returns 0 on success, or a subprocess.CalledProcessError instance on failure.
    """
    command, command_list = convert_command(command)

    log.info(f"Running {command}")
    try:
        subprocess.check_call(command_list)
    except subprocess.CalledProcessError as exc:
        log.error(f"{command_list[0]} exited with code {exc.returncode}")
        clear_junk()
        return exc
    clear_junk()
    return 0


def prun_multi(commands):
    """
    Runs multiple commands given by list.
    Calls clear_junk() afterwards.

    Returns list of ("command_string", exception) tuples, one for every command that failed.
    Empty list means all commands succeeded.
    """

    errors = []
    for command in commands:
        command, command_list = convert_command(command)
        log.info(f"Running {command}")
        try:
            subprocess.check_call(command_list)
        except subprocess.CalledProcessError as exc:
            log.error(f"{command_list[0]} exited with code {exc.returncode}")
            errors.append((command, exc))
    clear_junk()
    return errors


def clear_junk():
    """
    Clears junk output products according to cab "junk" config variable
    """
    for item in config.get("junk", []):
        for dest in [OUTPUT, MSDIR]:  # these are the only writable volumes in the container
            items = glob.glob(f"{dest}/{item}")
            if items:
                log.debug(f"clearing junk: {' '.join(items)}")
                for f in items:
                    if os.path.islink(f) or os.path.isfile(f):
                        os.remove(f)
                    elif os.path.isdir(f):
                        shutil.rmtree(f)

