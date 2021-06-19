#!/usr/bin/env python3

"""Intended to run on first boot. Expands the OS partitions to fill the SD card"""

import logging
import os
import sys
from subprocess import CalledProcessError, run
from typing import List

import parted
from parted.partition import Partition

log = logging.getLogger(__name__)
if __name__ == "__main__":
    logging.basicConfig(level='DEBUG')

def die(msg):
    log.critical(msg)
    sys.exit(1)    


def sh(command, *args, stdin=None, check=True):
    cmd = [command] + list(args)
    log.debug(f'run {cmd}')
    try:
        result = run(cmd, input=stdin, check=check, capture_output=True, text=True)
    except CalledProcessError as err:
        log.critical(err.stderr)
        die(f'{command} did not complete successfully')
    return result.stdout

def check(result, message):
    if not result:
        die(message)


def get_root_dev():
    part_dev = sh('findmnt', '-n', '-o', 'SOURCE', '/').strip()
    return '/dev/' + sh('lsblk', '-no', 'pkname', part_dev).strip()

def is_free_space(disk, start: int, end: int):
    free_spaces: List[parted.Geometry] = disk.getFreeSpaceRegions()
    for space in free_spaces:
        if start >= space.start and end <= space.end:
            return True
    return False

def create_partition(disk: parted.Disk, start, end, fscode, fstype=None):
    log.info(f'Creating partition ({start}-{end}) type {fscode}')
    geometry = parted.Geometry(device=disk.device, start=start, end=end)
    new_partition = parted.Partition(
        disk=disk,
        type=parted.PARTITION_NORMAL,
        fs=parted.FileSystem(type=fscode, geometry=geometry),
        geometry=geometry
    )
    if not disk.addPartition(partition=new_partition, constraint=parted.Constraint(exactGeom=geometry)):
        die(f'Creating partition failed')
    disk.commit()
    if fstype:
        sh(f'mkfs.{fstype}', new_partition.path)

def resize_partition(partition: parted.Partition, end):
    log.info(f'Resizing partition {partition.number} to end {end}')
    start = partition.geometry.start
    disk = partition.disk
    new_geometry = parted.Geometry(device=disk.device, start=start, end=end)
    if not disk.maximizePartition(partition=partition, constraint=parted.Constraint(exactGeom=new_geometry)):
        die(f'Partition {partition.path} resize failed')
    disk.commit()
    # Not on the path
    sh('/usr/sbin/resize2fs', partition.path)

def move_partition(partition: parted.Partition, start=None, end=None):
    log.debug(f'move_partition({partition}, {start}, {end})')
    if (start is None and end is None) or (start is not None and end is not None):
        raise ValueError('Exactly one of start or end must be specified')

    existing_geometry: parted.Geometry = partition.geometry
    if start is None:
        start = end - partition.getLength()
    elif end is None:
        end = start + partition.getLength()
    log.debug(f'New calculated position {start}-{end}')
    # Confirm that we're moving into free space
    disk = partition.disk
    if not is_free_space(disk, start, end):
        die(f'Trying to move partition {partition.number} into a space already occupied')
    # Physically move - afaict libparted cannot natively move a partition
    # Not efficient because it's not filesystem aware, but adequate for this purpose
    device = disk.device
    sec_size = device.sectorSize
    old_start_b = existing_geometry.start * sec_size
    new_start_b = start * sec_size
    length_b = int(existing_geometry.getLength('B'))
    log.info(f'Moving partition {partition.number} from {existing_geometry.start}s to {start}s...')
    with open(device.path, 'rb') as read_f, open(device.path, 'wb') as write_f:
        read_f.seek(old_start_b)
        write_f.seek(new_start_b)
        written = 0
        BUFF_SIZE = 64 * 1024
        percentage = 0
        while written < length_b:
            remaining = length_b - written
            buff = read_f.read(min(remaining, BUFF_SIZE))
            write_f.write(buff)
            written += len(buff)
            new_percentage = int(written / length_b * 10000) / 100
            if (new_percentage - percentage) >= 1:
                percentage = new_percentage
                log.info(f'{percentage}% moved ({written}/{length_b})')
    # Delete and recreate the old partition
    fscode = partition.fileSystem.type
    check(disk.deletePartition(partition), 'Deletion of old partition failed')
    disk.commit()
    create_partition(disk, start, end, fscode)


# Validate initial partition layout
root_dev = get_root_dev()
log.info(f'Root device is {root_dev}')
device: parted.Device = parted.getDevice(root_dev)
disk: parted.Disk = parted.newDisk(device)
log.info(f'Sector sizes {device.sectorSize}B/{device.physicalSectorSize}B')
partitions: List[parted.Partition] = disk.partitions    

device_size = device.getLength()
log.info(f'Disk size is {device_size}s')
sec_size = device.sectorSize
MIN_OS_PART_SIZE = int((4 * 1024**3) / sec_size)

# Although there's been an attempt to write this in a generic way, in reality it's only been tested with this table
# And only tested as laid out by the bootstrap script. Most importantly it assumes a contiguous layout.
PART_TABLE = [
    # fs,      partfs,   min_size (implies expansion)
    ('fat',  'fat32',  0),
    ('ext4', 'ext2',   MIN_OS_PART_SIZE),
    ('ext4', 'ext2',   MIN_OS_PART_SIZE),
    ('fat',  'fat32',  0)
]

log.info(f'Current partitions (s): {[ (p.fileSystem.type, p.getLength()) for p in partitions ]}')
# mkfs.vfat chooses 12/16/32 depending on FS size. As long as it's fat that's fine.
actual_types = [ 'fat' if p.fileSystem.type.startswith('fat') else p.fileSystem.type for p in partitions ]
expected_types = [ fs for fs, _, _ in PART_TABLE ]
if len(actual_types) > len(expected_types):
    die('There are more partitions on the device than expected')
if len(expected_types) > len(actual_types):
    die('There are fewer partitions on the device than expected')
if expected_types != actual_types:
    die(f'The device partitions are of a different type to what was expected')

free_space: parted.Geometry = disk.getFreeSpaceRegions()[-1]
dynamic_partitions = [ (n, size) for n, (_, _, size) in enumerate(PART_TABLE) if size > 0 ]
dynamic_part_nums = [ n + 1 for n, _ in dynamic_partitions ]
existing_parts_size = sum([ p.getLength() for p in partitions if p.number in dynamic_part_nums ])
new_dynamic_part_size = int((free_space.getLength() + existing_parts_size) / len(dynamic_partitions))
for _, min_size in dynamic_partitions:
    if min_size > new_dynamic_part_size:
        die(f'Disk is too small to accomodate partitions\' required sizes ({dynamic_partitions})')

for n, (fs, partfs, min_size) in reversed(list(enumerate(PART_TABLE))):
    free_spaces = disk.getFreeSpaceRegions()
    if not free_spaces:
        log.info('No free space left on disk, nothing more to do')
        break
    free_space: parted.Geometry = free_spaces[-1]
    part_num = n + 1
    existing_part: parted.Partition = disk.partitions[n]
    if free_space.end < existing_part.geometry.start:
        log.info(f'No free space after partition {part_num}, assuming this has already been moved')
        continue
    if min_size > 0: # dynamic size
        new_end = free_space.end
        new_start = free_space.end - new_dynamic_part_size
        # The first dynamic partition should remain fixed in place
        if existing_part.number > dynamic_part_nums[0]:
            move_partition(existing_part, start=new_start)
        # Partition will have been recreated
        existing_part: parted.Partition = disk.partitions[n]
        resize_partition(existing_part, new_end)
    elif free_space.start > existing_part.geometry.end: # can move
        move_partition(existing_part, end=free_space.end)

log.info('Done')
