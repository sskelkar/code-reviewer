"""Per-job Linux TAP policy and standard CONNECT proxy; never handles API credentials."""
from __future__ import annotations

import os
import ipaddress
import json
import pwd
import signal
import socket
import subprocess
import time
from pathlib import Path

TAP = 'cr-review0'
TABLE = 'cr_reviewer'
GATEWAY = '192.0.2.1'
GUEST = '192.0.2.2'
PROXY = (GATEWAY, 3128)


def firewall(proxy_uid=13, resolvers=('127.0.0.53',)):
    if type(proxy_uid) is not int or proxy_uid < 1:
        raise ValueError('invalid proxy UID')
    dns = ', '.join(str(ipaddress.IPv4Address(address)) for address in resolvers)
    if not dns:
        raise ValueError('trusted IPv4 resolver required')
    return '''table inet cr_reviewer {
 chain input { type filter hook input priority -300; policy accept;
  iifname "cr-review0" ip saddr 192.0.2.2 ip daddr 192.0.2.1 tcp dport 3128 accept
  iifname "cr-review0" drop
 }
 chain forward { type filter hook forward priority -300; policy accept;
  iifname "cr-review0" drop
  oifname "cr-review0" drop
 }
 chain output { type filter hook output priority 0; policy accept;
  oifname "cr-review0" ip daddr 192.0.2.2 tcp sport 3128 ct state established accept
  oifname "cr-review0" drop
  meta skuid PROXY_UID oifname "lo" ip daddr 192.0.2.1 tcp sport 3128 ct state established accept
  meta skuid PROXY_UID ip daddr { DNS_ADDRESSES } udp dport 53 accept
  meta skuid PROXY_UID ip daddr { DNS_ADDRESSES } tcp dport 53 accept
  meta skuid PROXY_UID meta nfproto ipv6 reject
  meta skuid PROXY_UID ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 } reject
  meta skuid PROXY_UID tcp dport 443 accept
  meta skuid PROXY_UID reject
 }
}'''.replace('PROXY_UID', str(proxy_uid)).replace('DNS_ADDRESSES', dns)


def squid_config(folder, phase='inference'):
    if phase not in ('acquisition', 'inference'):
        raise ValueError('invalid network phase')
    domains = 'github.com api.github.com' if phase == 'acquisition' else 'api.openai.com'
    authority = r'^(github\.com|api\.github\.com):443$' if phase == 'acquisition' else r'^api\.openai\.com:443$'
    # -n prevents reverse-DNS conversion from turning a literal IP into an allowed name.
    return '''http_port 192.0.2.1:3128
workers 1
acl source src 192.0.2.2/32
acl tunnel method CONNECT
acl secure port 443
acl provider dstdomain -n ALLOWED_DOMAINS
acl exact_uri url_regex -i ALLOWED_AUTHORITY
acl forbidden dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10
http_access deny !source
http_access deny !tunnel
http_access deny !secure
http_access deny !provider
http_access deny !exact_uri
http_access deny forbidden
http_access allow source tunnel secure provider exact_uri
http_access deny all
cache deny all
cache_mem 0 MB
access_log none
cache_log /dev/null
cache_store_log none
pid_filename none
netdb_filename none
pinger_enable off
shutdown_lifetime 0 seconds
max_filedescriptors 256
visible_hostname reviewer-proxy
forwarded_for delete
coredump_dir %s
'''.replace('ALLOWED_DOMAINS', domains).replace('ALLOWED_AUTHORITY', authority) % folder


def command(args, *, text=None, check=True):
    result = subprocess.run(args, input=text, capture_output=True, text=True, timeout=20,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
    if check and result.returncode:
        raise RuntimeError('sandbox infrastructure command failed: ' + args[0])
    return result


class Network:
    def __init__(self, root, uid, timeout=900, *, acquisition=False):
        self.root, self.uid, self.timeout = Path(root), uid, timeout
        self.tap = self.table = False
        self.proxy = None
        self.phase = 'acquisition' if acquisition else 'inference'
        self.deadline = time.monotonic() + timeout

    def __enter__(self):
        try:
            if command(['ip', 'link', 'show', TAP], check=False).returncode == 0:
                raise RuntimeError('sandbox TAP already exists; inspect stale infrastructure')
            if command(['nft', 'list', 'table', 'inet', TABLE], check=False).returncode == 0:
                raise RuntimeError('sandbox firewall table already exists; inspect stale infrastructure')
            routes = json.loads(command(['ip', '-j', 'route', 'show']).stdout)
            for route in routes:
                destination = route.get('dst', 'default')
                if destination != 'default' and ipaddress.ip_network(destination).overlaps(ipaddress.ip_network('192.0.2.0/30')):
                    raise RuntimeError('sandbox subnet overlaps an existing host route')
            user = pwd.getpwnam('proxy')
            resolver_text = Path('/etc/resolv.conf').read_text()
            resolvers = [line.split()[1] for line in resolver_text.splitlines()
                         if line.split()[:1] == ['nameserver'] and ':' not in line.split()[1]][:3]
            command(['nft', '-f', '-'], text=firewall(user.pw_uid, resolvers))
            self.table = True
            command(['ip', 'tuntap', 'add', TAP, 'mode', 'tap', 'user', str(self.uid)])
            self.tap = True
            command(['ip', 'addr', 'add', GATEWAY + '/30', 'dev', TAP])
            command(['ip', 'link', 'set', TAP, 'up'])
            folder = self.root / 'proxy'
            folder.mkdir(mode=0o700)
            user = pwd.getpwnam('proxy')
            os.chown(folder, user.pw_uid, user.pw_gid)
            self.start_proxy()
            command(['nft', 'list', 'table', 'inet', TABLE])
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def stop_proxy(self):
        if self.proxy is not None:
            try:
                os.killpg(self.proxy.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proxy.wait(timeout=10)
            self.proxy = None

    def inference(self):
        if self.phase != 'acquisition' or not self.tap or not self.table:
            raise ValueError('source access can only be revoked once in an active job')
        # Kill the old proxy group: established GitHub tunnels must close too.
        self.stop_proxy()
        self.phase = 'inference'
        self.start_proxy()
        command(['nft', 'list', 'table', 'inet', TABLE])

    def start_proxy(self):
        remaining = int(self.deadline - time.monotonic())
        if remaining < 1:
            raise TimeoutError('network lifetime exhausted')
        folder = self.root / 'proxy'
        user = pwd.getpwnam('proxy')
        config = self.root / 'proxy.conf'
        config.write_text(squid_config(folder, self.phase))
        config.chmod(0o644)  # Parent/config are root-owned; the proxy cannot replace policy.
        # Run in foreground, unprivileged, without enabling the system Squid service.
        def demote():
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            os.setgroups([])
            os.setgid(user.pw_gid)
            os.setuid(user.pw_uid)
        self.proxy = subprocess.Popen(['/usr/bin/timeout', '--signal=KILL', str(remaining), '/usr/sbin/squid', '-N', '-f', str(config)],
                                      cwd=str(folder), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True,
                                      preexec_fn=demote, env={'PATH': '/usr/bin:/bin'})
        for _ in range(100):
            if self.proxy.poll() is not None:
                raise RuntimeError('mandatory proxy failed to start')
            try:
                with socket.create_connection(PROXY, timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError('mandatory proxy startup deadline exceeded')

    def __exit__(self, *args):
        failures = []
        self.stop_proxy()
        # Remove the TAP first: a cleanup failure must never leave a live unfiltered guest route.
        if self.tap:
            if command(['ip', 'link', 'del', TAP], check=False).returncode:
                failures.append('TAP')
            else:
                self.tap = False
        if self.table and not self.tap:
            if command(['nft', 'delete', 'table', 'inet', TABLE], check=False).returncode:
                failures.append('firewall')
            else:
                self.table = False
        if failures and args[0] is None:
            raise RuntimeError('sandbox cleanup incomplete: ' + ', '.join(failures))
