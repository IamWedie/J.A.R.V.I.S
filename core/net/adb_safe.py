import posixpath
import re
import shlex


def safe_package(value):
    if not isinstance(value, str):
        raise ValueError("invalid package name")
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", value):
        raise ValueError("invalid package name")
    return value


def safe_phone_number(value):
    if not isinstance(value, str):
        raise ValueError("invalid phone number")
    if not re.fullmatch(r"\+?\d{3,15}", value):
        raise ValueError("invalid phone number")
    return value


def safe_remote_path(value):
    if not isinstance(value, str):
        raise ValueError("invalid remote path")
    if not value:
        raise ValueError("invalid remote path")
    for ch in value:
        if ord(ch) < 32 or ord(ch) == 127:
            raise ValueError("invalid remote path")
    forbidden = set("'\"`$;|&\\<>(){}[]*?!#~")
    if any(ch in forbidden for ch in value):
        raise ValueError("invalid remote path")
    for segment in value.split("/"):
        if segment == "..":
            raise ValueError("invalid remote path")
    normalized = posixpath.normpath(value)
    if normalized != "/sdcard" and not normalized.startswith("/sdcard/"):
        raise ValueError("invalid remote path")
    return normalized


def quote_shell_arg(value):
    if value is None:
        raise ValueError("invalid shell argument")
    return shlex.quote(str(value))


def build_shell_command(argv):
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of arguments, not a string")
    return " ".join(quote_shell_arg(item) for item in argv)
