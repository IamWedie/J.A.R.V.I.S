import os
import time


def echo(value):
    return value


def sleep(seconds):
    time.sleep(seconds)
    return "done"


def boom():
    raise RuntimeError("boom")


def env_keys():
    return sorted(os.environ)


def write_marker(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("ran")
    return "ok"
