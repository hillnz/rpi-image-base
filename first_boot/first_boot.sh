#!/bin/sh

mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t tmpfs tmp /run
mkdir -p /run/systemd

echo 1 > /proc/sys/kernel/sysrq

mount / -o remount,rw # rw is required for online resize
if /first_boot/expand_partitions.py; then
  # Remove init from kernel args
  mount /boot
  sed -i 's| init=/first_boot/first_boot\.sh||' /boot/cmdline.txt
  mount /boot -o remount,ro
  sync
fi
sleep 5

# reboot
mount / -o remount,ro
umount /boot
sync
echo b > /proc/sysrq-trigger
sleep 5
exit 0
