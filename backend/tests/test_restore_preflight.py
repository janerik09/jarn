import asyncio

import pytest

from app.services.backup_providers import BackupError, ProxmoxProvider, parse_disks, parse_size, _config_items

VOLID = "backup-store:backup/vm/4002/2026-01-01T00:00:00Z"
EXTRACTED = "cores: 2\nscsi0: storage-a:vm-4002-disk-0,size=10G\nide2: none,media=cdrom\nnet0: virtio=AA:BB,bridge=vmbr0\n"


def test_parse_size_units():
    assert parse_size("32G") == 32 * 1024 ** 3
    assert parse_size("512M") == 512 * 1024 ** 2
    assert parse_size("1.5T") == int(1.5 * 1024 ** 4)
    assert parse_size("bogus") is None


def test_parse_disks_skips_cdrom_and_non_disk_keys():
    items = _config_items(EXTRACTED)
    disks = parse_disks(items, "qemu")
    assert [d["key"] for d in disks] == ["scsi0"]
    assert disks[0]["storage"] == "storage-a"
    assert disks[0]["size_bytes"] == 10 * 1024 ** 3


def test_parse_disks_lxc_rootfs_and_mountpoints():
    disks = parse_disks([("rootfs", "local-lvm:vm-101-disk-0,size=8G"), ("mp0", "/mnt/host,mp=/data"),
                         ("mp1", "nas:subvol-101-disk-1,size=2G")], "lxc")
    assert [d["key"] for d in disks] == ["rootfs", "mp1"]


class FakeProxmox(ProxmoxProvider):
    """Answers the Proxmox calls the preflight makes, from a dict. A value that is an Exception is raised."""

    def __init__(self, routes):
        super().__init__("https://pve.test:8006", "backup@pve!jarn", "secret")
        self.routes = routes

    async def _request(self, method, path, data=None, params=None):
        value = self.routes[(method, path)]
        if isinstance(value, Exception):
            raise value
        return value


def base_routes(vm_present=True, storages=None, perms=None):
    storages = storages if storages is not None else [
        {"storage": "storage-a", "type": "lvmthin", "active": 1, "enabled": 1, "avail": 5 * 1024 ** 3,
         "total": 100 * 1024 ** 3, "content": "images,rootdir"},
        {"storage": "fast", "type": "zfspool", "active": 1, "enabled": 1, "avail": 50 * 1024 ** 3,
         "total": 60 * 1024 ** 3, "content": "images"},
        {"storage": "backup-store", "type": "pbs", "active": 1, "enabled": 1, "avail": 900 * 1024 ** 3,
         "total": 1000 * 1024 ** 3, "content": "backup"},
    ]
    perms = perms if perms is not None else {"/storage/storage-a": {"Datastore.AllocateSpace": 1},
                                             "/storage/fast": {"Datastore.AllocateSpace": 1}}
    return {
        ("GET", "/nodes/pve1/storage/backup-store/content"): [{"volid": VOLID, "size": 1000}],
        ("GET", "/nodes/pve1/vzdump/extractconfig"): EXTRACTED,
        ("GET", "/nodes/pve1/qemu/4002/config"): {"scsi0": "storage-a:vm-4002-disk-0,size=10G"} if vm_present
        else BackupError("no such VM"),
        ("GET", "/nodes/pve1/storage"): storages,
        ("GET", "/access/permissions"): perms,
    }


def run(routes, **kwargs):
    provider = FakeProxmox(routes)
    return asyncio.run(provider.restore_preflight("pve1", "qemu", 4002, VOLID, **kwargs))


def test_default_restore_to_original_storage_passes_when_vm_disks_are_freed():
    # Needs 11 GiB on storage-a (10 GiB plus headroom). 5 GiB free plus the 10 GiB the current VM frees is enough.
    result = run(base_routes())
    assert result["ok"], result["errors"]
    assert result["disks"][0]["target_storage"] == "storage-a"


def test_restore_fails_without_freed_space_and_nothing_is_stopped():
    result = run(base_routes(vm_present=False))
    assert not result["ok"]
    assert any("storage-a" in e and "needs" in e for e in result["errors"])


def test_target_storage_override_sends_every_disk_to_chosen_storage():
    result = run(base_routes(vm_present=False), target_storage="fast")
    assert result["ok"], result["errors"]
    assert result["disks"][0]["target_storage"] == "fast"


def test_missing_target_storage_is_reported():
    result = run(base_routes(), target_storage="nope")
    assert not result["ok"]
    assert any("storage does not exist" in e for e in result["errors"])


def test_storage_that_rejects_images_is_reported():
    storages = base_routes()[("GET", "/nodes/pve1/storage")]
    storages[1] = {**storages[1], "content": "backup"}
    result = run(base_routes(storages=storages), target_storage="fast")
    assert not result["ok"]
    assert any("does not accept images" in e for e in result["errors"])


def test_missing_allocate_permission_blocks_restore():
    result = run(base_routes(perms={}))
    assert not result["ok"]
    assert any("Datastore.AllocateSpace" in e for e in result["errors"])


def test_candidates_mark_storages_that_could_hold_whole_vm():
    result = run(base_routes(vm_present=False))
    fits = {c["storage"]: c["fits"] for c in result["candidates"]}
    assert fits["fast"] is True
    assert fits["storage-a"] is False
    assert "backup-store" not in fits  # backup storages do not hold guest disks


def test_unreadable_backup_stops_early():
    routes = base_routes()
    routes[("GET", "/nodes/pve1/vzdump/extractconfig")] = BackupError("500 from Proxmox")
    result = run(routes)
    assert not result["ok"]
    assert result["checks"][-1]["name"] == "backup_readable"



def test_malformed_backup_identifier_is_rejected_before_any_call():
    with pytest.raises(BackupError, match="backup identifier"):
        asyncio.run(FakeProxmox({}).restore_preflight("pve1", "qemu", 4002, ""))
