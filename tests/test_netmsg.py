"""Tests for core.net.netmsg module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_netmsg_import():
    from core.net import netmsg
    assert callable(netmsg.send_message)
    assert callable(netmsg.send_to_device)
    assert callable(netmsg.send_broadcast)
    assert callable(netmsg.ping_device)
    assert callable(netmsg.on_message)
    assert callable(netmsg.get_messages)
    assert callable(netmsg.start_receiver)
