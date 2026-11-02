#!/usr/bin/env python3
# ==============================================================================
# Calamares Custom Job Module: Limine Bootloader (BIOS + UEFI) & Golden Master
# ==============================================================================
import os
import re
import shutil
import signal
import subprocess
import time

try:
    import libcalamares
except ImportError:
    libcalamares = None


def pretty_name():
    return "Installing Limine Bootloader (BIOS + UEFI) and creating Golden Master Btrfs Snapshot"


def _log_warn(msg):
    if libcalamares is not None and hasattr(libcalamares, "utils"):
        libcalamares.utils.warning(msg)


def _run_host(cmd, check=True, input_text=None):
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _clean_dev_path(source):
    if not source:
        return ""
    cleaned = re.sub(r"\[.*\]$", "", source.strip()).strip()
    return cleaned


def _make_part_path(parent_disk, part_num):
    if parent_disk and parent_disk[-1].isdigit():
        return f"{parent_disk}p{part_num}"
    return f"{parent_disk}{part_num}"


def _split_disk_and_part(dev_path):
    dev_path = _clean_dev_path(dev_path)
    if not dev_path.startswith("/dev/"):
        raise ValueError(f"Invalid block device path: {dev_path!r}")

    m = re.match(
        r"^(/dev/(?:nvme\d+n\d+|mmcblk\d+|loop\d+|md\d+|nbd\d+))p(\d+)$",
        dev_path,
    )
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"^(/dev/[a-zA-Z]+)(\d+)$", dev_path)
    if m:
        return m.group(1), int(m.group(2))

    pk_res = _run_host(["lsblk", "-ndo", "PKNAME", dev_path], check=False)
    pkname = pk_res.stdout.strip().splitlines()[0].strip() if pk_res.stdout else ""
    sys_part = f"/sys/class/block/{os.path.basename(dev_path)}/partition"
    if pkname and os.path.isfile(sys_part):
        with open(sys_part, "r", encoding="utf-8") as fh:
            return f"/dev/{pkname}", int(fh.read().strip())

    raise ValueError(f"Unable to split disk and partition number from {dev_path!r}")


def _is_mounted_partition(target_dir):
    if os.path.ismount(target_dir):
        return True
    res = _run_host(["findmnt", "-rn", "-M", target_dir], check=False)
    return res.returncode == 0 and bool(res.stdout.strip())


def _get_mount_source(mount_dir, gs_partitions=None, rel_mount="/"):
    flags = ("-M", "--target") if rel_mount == "/" else ("-M",)
    for flag in flags:
        res = _run_host(["findmnt", "-rn", "-o", "SOURCE", flag, mount_dir], check=False)
        if res.returncode == 0 and res.stdout.strip():
            dev = _clean_dev_path(res.stdout.strip().splitlines()[0])
            if dev.startswith("/dev/"):
                return dev

    if isinstance(gs_partitions, list):
        for part in gs_partitions:
            if isinstance(part, dict) and part.get("mountPoint") == rel_mount:
                dev = _clean_dev_path(str(part.get("device", "")))
                if dev.startswith("/dev/"):
                    return dev
    return ""


def _get_device_uuid(dev_path):
    dev_path = _clean_dev_path(dev_path)
    if not dev_path:
        return ""
    res = _run_host(["blkid", "-s", "UUID", "-o", "value", dev_path], check=False)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().splitlines()[0].strip().strip("\"'")
    return ""


def _parse_parted_mib(parent_disk):
    res = _run_host(["parted", "-sm", parent_disk, "unit", "MiB", "print"], check=True)
    pttable = "gpt"
    parts = {}
    for raw_line in res.stdout.splitlines():
        line = raw_line.strip().rstrip(";")
        if not line or line == "BYT":
            continue
        fields = line.split(":")
        if fields[0].startswith("/dev/") and len(fields) >= 6:
            pttable = fields[5].strip().lower()
        elif fields[0].isdigit() and len(fields) >= 4:
            pnum = int(fields[0])
            start_mib = int(float(fields[1].replace("MiB", "")))
            end_mib = int(float(fields[2].replace("MiB", "")))
            flags = fields[6].strip() if len(fields) >= 7 else ""
            name = fields[5].strip() if len(fields) >= 6 else ""
            parts[pnum] = {
                "start_mib": start_mib,
                "end_mib": end_mib,
                "name": name,
                "flags": flags,
            }
    return pttable, parts


def _resize_partition_end_mib(parent_disk, part_num, start_mib, new_end_mib):
    new_size_mib = new_end_mib - start_mib
    if new_size_mib <= 0:
        raise ValueError(
            f"Invalid target partition geometry: start={start_mib}MiB, end={new_end_mib}MiB"
        )
    _run_host(
        ["sfdisk", "--no-reread", "--force", "-N", str(part_num), parent_disk],
        check=True,
        input_text=f", {new_size_mib}MiB\n",
    )
    _run_host(["partx", "-u", parent_disk], check=False)
    _run_host(["udevadm", "settle"], check=False)


def _append_efi_fstab(root_mount_point, efi_uuid):
    fstab_path = os.path.join(root_mount_point, "etc/fstab")
    os.makedirs(os.path.dirname(fstab_path), exist_ok=True)
    existing = ""
    if os.path.isfile(fstab_path):
        with open(fstab_path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    for line in existing.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = stripped.split()
        if len(cols) >= 2 and cols[1] == "/boot/efi":
            return
    entry = f"UUID={efi_uuid} /boot/efi vfat defaults,umask=0077 0 2\n"
    with open(fstab_path, "a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(entry)


def _ensure_efi_partition_and_bios_grub(root_mount_point, gs_partitions=None):
    efi_mount_dir = os.path.join(root_mount_point, "boot/efi")
    os.makedirs(efi_mount_dir, exist_ok=True)

    root_dev = _get_mount_source(root_mount_point, gs_partitions, "/")
    if not root_dev:
        raise RuntimeError(f"Unable to determine root block device for {root_mount_point}")
    parent_disk, root_part_num = _split_disk_and_part(root_dev)

    if not _is_mounted_partition(efi_mount_dir):
        preexisting_efi_dev = _get_mount_source(efi_mount_dir, gs_partitions, "/boot/efi")
        if preexisting_efi_dev and preexisting_efi_dev != root_dev and os.path.exists(preexisting_efi_dev):
            _run_host(["mount", preexisting_efi_dev, efi_mount_dir], check=True)

    if _is_mounted_partition(efi_mount_dir):
        # UEFI install mode: /boot/efi is already a mounted partition.
        # Do NOT shrink the mounted Btrfs root partition.
        efi_dev = _get_mount_source(efi_mount_dir, gs_partitions, "/boot/efi")
        if not efi_dev:
            raise RuntimeError(f"Unable to determine mounted /boot/efi device at {efi_mount_dir}")
        _, efi_part_num = _split_disk_and_part(efi_dev)
        return parent_disk, root_dev, efi_part_num

    # BIOS install mode: Calamares only created the Btrfs root partition.
    # Dynamically shrink Btrfs root filesystem by 2132 MiB and shrink the root
    # partition by 2100 MiB via sfdisk (without calling `btrfs filesystem resize max`,
    # which would expand Btrfs back to the kernel's cached unshrunk block device size),
    # then create a 2048 MiB FAT32 partition (NOVOS_EFI) (and a 2 MiB bios_grub
    # partition if GPT) via sfdisk -a, register device nodes with partx, mount at
    # /boot/efi, and append UUID= to /etc/fstab.
    backup_dir = "/tmp/novos-efi-premount-backup"
    shutil.rmtree(backup_dir, ignore_errors=True)
    os.makedirs(backup_dir, exist_ok=True)
    for item in os.listdir(efi_mount_dir):
        src_item = os.path.join(efi_mount_dir, item)
        dst_item = os.path.join(backup_dir, item)
        if os.path.isdir(src_item):
            shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
        else:
            shutil.copy2(src_item, dst_item)

    _run_host(["btrfs", "filesystem", "resize", "-2132M", root_mount_point], check=True)
    _run_host(["sync"], check=False)

    pttable, parts = _parse_parted_mib(parent_disk)
    if root_part_num not in parts:
        raise RuntimeError(f"Root partition {root_part_num} not found in parted table for {parent_disk}")

    root_start_mib = parts[root_part_num]["start_mib"]
    root_end_mib = parts[root_part_num]["end_mib"]
    new_root_end_mib = root_end_mib - 2100
    if new_root_end_mib <= root_start_mib + 1024:
        raise RuntimeError(
            f"Insufficient space on {root_dev} ({root_end_mib} MiB) to shrink by 2100 MiB"
        )

    _resize_partition_end_mib(parent_disk, root_part_num, root_start_mib, new_root_end_mib)

    pttable, parts = _parse_parted_mib(parent_disk)
    before_pnums = set(parts.keys())

    if pttable == "gpt":
        _run_host(
            ["sfdisk", "--no-reread", "--force", "-a", parent_disk],
            check=True,
            input_text=", 2MiB, 21686148-6449-6E6F-744E-656564454649\n",
        )
        _, parts_after_bios = _parse_parted_mib(parent_disk)
        new_bios_pnums = sorted(set(parts_after_bios.keys()) - before_pnums)
        bios_part_num = (
            new_bios_pnums[0]
            if new_bios_pnums
            else (max(before_pnums, default=root_part_num) + 1)
        )

        before_efi_pnums = set(parts_after_bios.keys()) | {bios_part_num}
        _run_host(
            ["sfdisk", "--no-reread", "--force", "-a", parent_disk],
            check=True,
            input_text=", 2048MiB, C12A7328-F81F-11D2-BA4B-00A0C93EC93B\n",
        )
        _, parts_after_efi = _parse_parted_mib(parent_disk)
        new_efi_pnums = sorted(set(parts_after_efi.keys()) - before_efi_pnums)
        efi_part_num = (
            new_efi_pnums[0]
            if new_efi_pnums
            else (max(before_efi_pnums, default=bios_part_num) + 1)
        )
    else:
        _run_host(
            ["sfdisk", "--no-reread", "--force", "-a", parent_disk],
            check=True,
            input_text=", 2048MiB, c, *\n",
        )
        _, parts_after_efi = _parse_parted_mib(parent_disk)
        new_efi_pnums = sorted(set(parts_after_efi.keys()) - before_pnums)
        efi_part_num = (
            new_efi_pnums[0]
            if new_efi_pnums
            else (max(before_pnums, default=root_part_num) + 1)
        )

    _run_host(["partx", "-a", parent_disk], check=False)
    _run_host(["partx", "-u", parent_disk], check=False)
    _run_host(["udevadm", "settle"], check=False)

    efi_dev = _make_part_path(parent_disk, efi_part_num)
    if not os.path.exists(efi_dev):
        _run_host(["partx", "-a", "--nr", str(efi_part_num), parent_disk], check=False)
        _run_host(["udevadm", "settle"], check=False)
        for _ in range(10):
            if os.path.exists(efi_dev):
                break
            time.sleep(0.2)

    _run_host(["mkfs.vfat", "-F", "32", "-n", "NOVOS_EFI", efi_dev], check=True)
    _run_host(
        ["mount", "-t", "vfat", "-o", "defaults,umask=0077", efi_dev, efi_mount_dir],
        check=True,
    )

    for item in os.listdir(backup_dir):
        src_item = os.path.join(backup_dir, item)
        dst_item = os.path.join(efi_mount_dir, item)
        if os.path.isdir(src_item):
            shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
        else:
            shutil.copy2(src_item, dst_item)
    shutil.rmtree(backup_dir, ignore_errors=True)

    efi_uuid = _get_device_uuid(efi_dev)
    if not efi_uuid:
        raise RuntimeError(f"Failed to obtain FAT32 UUID for newly formatted {efi_dev}")
    _append_efi_fstab(root_mount_point, efi_uuid)

    return parent_disk, root_dev, efi_part_num


def _install_limine_disk_and_nvram(parent_disk, efi_part_num):
    pttable, parts = _parse_parted_mib(parent_disk)
    sfdisk_dump = _run_host(["sfdisk", "-d", parent_disk], check=False)
    has_bios_grub = (
        any(
            "bios_grub" in p.get("flags", "").lower() or p.get("name", "").lower() == "bios_grub"
            for p in parts.values()
        )
        or "21686148-6449-6e6f-744e-656564454649" in (sfdisk_dump.stdout or "").lower()
    )

    # Install Limine Stage 1/2 to parent disk MBR or GPT bios_grub partition
    if pttable != "gpt" or has_bios_grub:
        limine_bin = "/usr/local/bin/limine"
        if not os.path.isfile(limine_bin):
            limine_bin = "/usr/share/limine/limine"
        _run_host([limine_bin, "bios-install", parent_disk], check=True)

    # Register NovOS in UEFI NVRAM if booted under UEFI
    if os.path.exists("/sys/firmware/efi"):
        res = _run_host(
            [
                "efibootmgr",
                "--create",
                "--disk",
                parent_disk,
                "--part",
                str(efi_part_num),
                "--label",
                "NovOS",
                "--loader",
                r"\EFI\BOOT\BOOTX64.EFI",
            ],
            check=False,
        )
        if res.returncode != 0:
            _log_warn(f"efibootmgr registration returned {res.returncode}: {res.stderr.strip()}")


def _verify_boot_artifacts(root_mount_point):
    efi_dirs = [
        os.path.join(root_mount_point, "boot/efi/boot/limine"),
        os.path.join(root_mount_point, "boot/efi/limine"),
        os.path.join(root_mount_point, "boot/efi/EFI/BOOT"),
        os.path.join(root_mount_point, "boot/efi"),
    ]
    primary_kernel = os.path.join(root_mount_point, "boot/efi/kernels/vmlinuz-primary")
    limine_confs = [os.path.join(d, "limine.conf") for d in efi_dirs]
    bootx64_files = [os.path.join(d, "BOOTX64.EFI") for d in efi_dirs]
    bios_sys_files = [os.path.join(d, "limine-bios.sys") for d in efi_dirs]
    wallpaper_files = [os.path.join(d, "nova-wallpaper.png") for d in efi_dirs]

    required_files = (
        [primary_kernel] + bootx64_files + bios_sys_files + wallpaper_files + limine_confs
    )
    missing = [p for p in required_files if not os.path.isfile(p) or os.path.getsize(p) == 0]
    if missing:
        raise RuntimeError(f"Missing required ESP boot artifacts: {', '.join(missing)}")

    for conf_path in limine_confs:
        with open(conf_path, "r", encoding="utf-8") as fh:
            conf_text = fh.read()

        if "root=UUID=NOVOS_LIVE" in conf_text:
            raise RuntimeError(
                f"Invalid root=UUID=NOVOS_LIVE placeholder in {conf_path}"
            )

        m = re.search(r"root=UUID=([0-9a-fA-F-]{8,})", conf_text)
        if not m:
            raise RuntimeError(
                f"Missing valid root=UUID=<uuid> kernel cmdline parameter in {conf_path}"
            )


def _terminate_target_processes(root_mount_point):
    if not root_mount_point:
        return
    norm_root = os.path.normpath(root_mount_point)
    real_root = os.path.realpath(norm_root)
    if norm_root == "/" or real_root == "/" or not os.path.isdir(norm_root):
        return

    my_pid = os.getpid()
    my_ppid = os.getppid()
    target_pids = []

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in (my_pid, my_ppid, 1):
            continue
        proc_root = f"/proc/{pid}/root"
        try:
            resolved = os.path.realpath(proc_root)
        except OSError:
            continue
        if (
            resolved == norm_root
            or resolved.startswith(norm_root + "/")
            or resolved == real_root
            or resolved.startswith(real_root + "/")
        ):
            target_pids.append(pid)

    if not target_pids:
        return

    for pid in target_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    time.sleep(0.5)

    for pid in target_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def run():
    if libcalamares is None:
        return ("Missing libcalamares", "Calamares Python bindings are not available.")

    root_mount_point = libcalamares.globalstorage.value("rootMountPoint")
    if not root_mount_point:
        return ("Missing rootMountPoint", "Calamares did not set rootMountPoint.")

    gs_partitions = libcalamares.globalstorage.value("partitions")

    try:
        parent_disk, root_dev, efi_part_num = _ensure_efi_partition_and_bios_grub(
            root_mount_point, gs_partitions
        )
    except Exception as exc:
        return ("Limine Partition Setup Failed", str(exc))

    root_uuid = _get_device_uuid(root_dev)

    commands = [
        # 1. Ensure chattr +C on container and LLM subvolumes
        "chattr +C /var/lib/docker /var/lib/containerd /srv/llm || true",
        # 2. Install Limine UEFI/BIOS binaries and Nova wallpaper from rootfs cache onto ESP
        "mkdir -p /boot/efi/boot/limine /boot/efi/limine /boot/efi/EFI/BOOT /boot/efi/kernels && "
        "for d in /boot/efi/boot/limine /boot/efi/limine /boot/efi/EFI/BOOT /boot/efi; do "
        '(cp -f /usr/share/limine/BOOTX64.EFI "$d/BOOTX64.EFI" || true); '
        '(cp -f /usr/share/limine/limine-bios.sys "$d/limine-bios.sys" || true); '
        '(cp -f /usr/share/backgrounds/novos/nova-wallpaper.png "$d/nova-wallpaper.png" || true); '
        "done",
        # 3. Rebuild initramfs and sync both XanMod + Fallback kernels to /boot/efi/kernels
        'for kver in $(ls -1 /lib/modules/ | sort -V); do [ -f "/boot/vmlinuz-${kver}" ] || continue; dracut --force --kver "$kver" "/boot/initrd.img-${kver}" || true; /etc/kernel/postinst.d/zz-sync-limine "$kver"; done',
        # 4. Enable Snapper root config and restore APT hooks on the installed target OS
        'sed -i \'s/^SNAPPER_CONFIGS=""/SNAPPER_CONFIGS="root"/\' /etc/default/snapper || true',
        "if [ -f /usr/share/novos/80snapper.disabled ]; then cp -f /usr/share/novos/80snapper.disabled /etc/apt/apt.conf.d/80snapper; fi",
        "if [ -f /usr/share/novos/99-limine-snapshots.disabled ]; then cp -f /usr/share/novos/99-limine-snapshots.disabled /etc/apt/apt.conf.d/99-limine-snapshots; fi",
        # 5. Take Golden Master Snapshot and generate /boot/efi/limine/limine.conf (mirrored across all ESP paths)
        'snapper --no-dbus -c root create --description "Golden Master Install" || true',
        f'NOVOS_ROOT_UUID="{root_uuid}" /usr/local/sbin/update-limine-snapshots && '
        "for d in /boot/efi/boot/limine /boot/efi/limine /boot/efi/EFI/BOOT /boot/efi; do "
        '([ "$d" = "/boot/efi/limine" ] || cp -f /boot/efi/limine/limine.conf "$d/limine.conf" || true); '
        "done",
    ]

    for cmd in commands:
        ret = libcalamares.utils.target_env_call(["/bin/bash", "-c", cmd])
        if ret != 0:
            _log_warn(f"Command returned {ret}: {cmd}")

    try:
        _install_limine_disk_and_nvram(parent_disk, efi_part_num)
        _verify_boot_artifacts(root_mount_point)
    except Exception as exc:
        _terminate_target_processes(root_mount_point)
        _run_host(["sync"], check=False)
        return ("Limine Bootloader Installation Failed", str(exc))

    _terminate_target_processes(root_mount_point)
    _run_host(["sync"], check=False)

    return None
