"""LAN addresses of this machine, for the dashboard link and discovery broadcasts."""

from __future__ import annotations

import ipaddress
import socket

import ifaddr

# Adapters that usually aren't the Wi-Fi/LAN the anchors share.
_VIRTUAL_HINTS = ("vethernet", "virtualbox", "vmware", "hyper-v", "wsl", "docker", "loopback", "tailscale", "zerotier", "vpn")


def lan_ipv4_addresses() -> list[dict]:
    """[{ip, prefix, interface, virtual}] for usable IPv4 addresses, best candidates first."""
    found = []
    for adapter in ifaddr.get_adapters():
        name = adapter.nice_name or adapter.name
        for ip in adapter.ips:
            if not isinstance(ip.ip, str):
                continue  # IPv6 entries are tuples
            addr = ipaddress.IPv4Address(ip.ip)
            if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
                continue
            virtual = any(h in name.lower() for h in _VIRTUAL_HINTS)
            found.append({"ip": ip.ip, "prefix": ip.network_prefix, "interface": name, "virtual": virtual})
    preferred = primary_ipv4()
    found.sort(key=lambda a: (a["ip"] != preferred, a["virtual"], not ipaddress.IPv4Address(a["ip"]).is_private))
    return found


def primary_ipv4() -> str | None:
    """The source address the OS would use for outbound traffic (no packet is sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1; UDP connect only picks a route
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def broadcast_targets() -> list[str]:
    """Limited broadcast plus each adapter's directed broadcast.

    Windows sends 255.255.255.255 out of one adapter only, so directed
    broadcasts are what reach P1 on multi-adapter laptops.
    """
    targets = ["255.255.255.255"]
    for addr in lan_ipv4_addresses():
        if addr["prefix"] < 31:
            net = ipaddress.IPv4Network(f"{addr['ip']}/{addr['prefix']}", strict=False)
            targets.append(str(net.broadcast_address))
    return list(dict.fromkeys(targets))
