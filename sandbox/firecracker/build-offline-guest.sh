#!/bin/sh
# Test-only image builder. Run inside the disposable Ubuntu ARM64 Lima VM.
set -eu
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
    echo 'Requires the disposable Ubuntu ARM64 Linux build VM.' >&2
    exit 2
fi
if [ "$#" -ne 2 ] && [ "$#" -ne 3 ] && [ "$#" -ne 4 ]; then
    echo 'usage: build-offline-guest.sh trusted-source.tar output-directory [openai-offline | connected | verified-claude-binary expected-sha256]' >&2
    exit 2
fi
if [ "$#" -eq 3 ] && [ "$3" != openai-offline ] && [ "$3" != connected ]; then
    echo 'The third argument must be openai-offline or connected.' >&2
    exit 2
fi
bundle=$(realpath "$1")
output=$(realpath -m "$2")
[ ! -e "$output" ] || { echo 'Output directory must be new.' >&2; exit 2; }
mkdir -m 700 "$output"
root="$output/root"
mkdir -p "$root/usr/bin" "$root/usr/lib" "$root/bin" "$root/dev" "$root/proc" "$root/sys" "$root/tmp" "$root/opt/reviewer"
ln -s usr/lib "$root/lib"
cp /bin/busybox "$root/bin/busybox"
ln -s busybox "$root/bin/sh"
cp /usr/bin/python3 "$root/usr/bin/python3"
cp /usr/bin/git "$root/usr/bin/git"
# This first image derives from the installed Ubuntu runtime. Record its versions
# and artifact hashes; it is repeatable scaffolding, not a reproducible production image.
cp -a /usr/lib/aarch64-linux-gnu "$root/usr/lib/"
cp -a /usr/lib/python3.12 "$root/usr/lib/"
cp -a /usr/lib/locale "$root/usr/lib/"
cp -a /usr/lib/ld-linux-aarch64.so.1 "$root/usr/lib/"
# Never invoke tar extraction on an unchecked archive, even in this prototype.
python3 - "$bundle" "$root/opt/reviewer" <<'PYBUILD'
import hashlib,json,sys,tarfile
from pathlib import Path, PurePosixPath
archive,root=Path(sys.argv[1]),Path(sys.argv[2])
if archive.stat().st_size > 20*1024*1024:
    raise ValueError('source archive too large')
with tarfile.open(archive, 'r:') as tar:
    members=tar.getmembers()
    if len(members)>1000 or sum(m.size for m in members)>16*1024*1024:
        raise ValueError('source archive limits exceeded')
    seen=set()
    for member in members:
        name=member.name
        path=PurePosixPath(name)
        if (not member.isfile() or path.is_absolute() or '..' in path.parts or name in seen
                or member.size>2*1024*1024
                or not (name=='bundle-manifest.json' or name.startswith('src/code_reviewer/'))):
            raise ValueError('invalid source member: '+name)
        seen.add(name)
    for member in members:
        path=root/member.name
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as out:
            out.write(tar.extractfile(member).read())
manifest=json.loads((root/'bundle-manifest.json').read_text())
if manifest['format']!=1 or set(manifest['files']) != seen-{'bundle-manifest.json'}:
    raise ValueError('source manifest coverage mismatch')
for name,digest in manifest['files'].items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
        raise ValueError('source digest mismatch: '+name)
PYBUILD
workload=smoke
if [ "$#" -eq 3 ]; then
    workload=openai_smoke
    if [ "$3" = connected ]; then workload=guest; fi
fi
if [ "$#" -eq 4 ]; then
    # This is a trusted operator artifact, never a repository-selected executable.
    python3 - "$3" "$4" "$root/usr/bin/claude" <<'PYCLAUDE'
import hashlib,shutil,sys
from pathlib import Path
source,digest,output=Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3])
if source.is_symlink() or not source.is_file() or source.stat().st_size>300000000:
    raise ValueError('invalid bounded Claude binary')
if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
    raise ValueError('invalid expected Claude digest')
with source.open('rb') as stream:
    actual=hashlib.file_digest(stream,'sha256').hexdigest()
if actual!=digest:
    raise ValueError('Claude binary digest mismatch')
shutil.copyfile(source,output)
output.chmod(0o755)
PYCLAUDE
    workload=runtime_probe
fi
chmod 1777 "$root/tmp"
cat > "$root/init" <<'INIT'
#!/bin/sh
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
# Fresh tmp/home; no inherited host credentials or guest networking is configured.
/bin/busybox mkdir -p /tmp/home
/bin/busybox env -i PATH=/usr/bin:/bin HOME=/tmp/home TMPDIR=/tmp LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PYTHONPATH=/opt/reviewer/src PYTHONDONTWRITEBYTECODE=1 GIT_CONFIG_NOSYSTEM=1 \
    /usr/bin/python3 -m code_reviewer.sandbox.smoke
status=$?
echo "REVIEWER_SMOKE_GUEST_STATUS=$status"
/bin/busybox poweroff -f
INIT
if [ "$workload" != smoke ]; then
    sed -i "s/code_reviewer.sandbox.smoke/code_reviewer.sandbox.$workload/" "$root/init"
fi
if [ "$workload" = guest ]; then
    mkdir -p "$root/etc/ssl/certs" "$root/work"
    cp /etc/ssl/certs/ca-certificates.crt "$root/etc/ssl/certs/"
    cat > "$root/init" <<'CONNECTED'
#!/bin/sh
set -eu
trap '/bin/busybox poweroff -f' EXIT
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
/bin/busybox mount -t tmpfs -o size=512m,nosuid,nodev,noexec tmpfs /work
/bin/busybox mkdir -p /work/home
/bin/busybox chown -R 65534:65534 /work
/bin/busybox mount --bind /work /tmp
/bin/busybox ip link set lo up
/bin/busybox ip addr add 192.0.2.2/30 dev eth0
/bin/busybox ip link set eth0 up
/bin/busybox ip route add default via 192.0.2.1 dev eth0
/bin/busybox env -i PATH=/usr/bin:/bin HOME=/work/home TMPDIR=/work LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PYTHONPATH=/opt/reviewer/src PYTHONDONTWRITEBYTECODE=1 GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null GIT_ATTR_NOSYSTEM=1 SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    /usr/bin/python3 -m code_reviewer.sandbox.guest
CONNECTED
fi
chmod 755 "$root/init"
PYTHONPATH="$root/opt/reviewer/src" python3 -m code_reviewer.sandbox.image "$root" "$output/initrd.cpio.gz"
python3 --version > "$output/runtime-versions.txt"
git --version >> "$output/runtime-versions.txt"
uname -srmo >> "$output/runtime-versions.txt"
sha256sum "$bundle" "$output/initrd.cpio.gz" > "$output/artifacts.sha256"
cat "$output/runtime-versions.txt" "$output/artifacts.sha256"
