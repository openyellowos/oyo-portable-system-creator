
# oyo-portable-system-creator 仕様書 v2

作成日: 2026-03-16

---

# 1. 概要

**oyo-portable-system-creator** は、既存の Linux システム（open.Yellow.os など）を
USB メモリへコピーし、**ポータブル Linux 環境**を作成するツールである。

特徴:

- 現在の Linux 環境をそのまま USB にコピー
- BIOS / UEFI 両対応
- BIOS / UEFI は v1 必須（Secure Boot は v1.1 以降の限定対応）
- rsync ベースの安全なコピー
- GUI / CLI 両対応
- USB 向けパフォーマンス最適化

---

# 2. システム構成

```
PC (実行環境)
│
├─ oyo-portable-system-creator (GUI / CLI)
│
├─ rsync
├─ parted / sfdisk
├─ mkfs
├─ grub-install
├─ update-initramfs
│
└─ USB device
      ├─ EFI partition
      └─ root filesystem
```

---

# 3. USBパーティション設計

USB ディスクは以下の構成で作成する。

| Partition | Size | Type | 用途 |
|----------|------|------|------|
| sdb1 | 512MB | FAT32 | EFI |
| sdb2 | 残り | ext4 | Linux root |

例:

```
/dev/sdb1  EFI System Partition
/dev/sdb2  Linux root
```

作成コマンド例:

```bash
parted /dev/sdb mklabel gpt
parted /dev/sdb mkpart EFI fat32 1MiB 513MiB
parted /dev/sdb set 1 esp on
parted /dev/sdb mkpart root ext4 513MiB 100%
```

---

# 4. ファイルコピー仕様

Linux システムコピーは **rsync** を使用する。

## rsync コマンド

```
rsync -aHAXx --numeric-ids --info=progress2 \
  --delete \
  --exclude=/dev/* \
  --exclude=/proc/* \
  --exclude=/sys/* \
  --exclude=/tmp/* \
  --exclude=/run/* \
  --exclude=/mnt/* \
  --exclude=/media/* \
  --exclude=/lost+found \
  / /mnt/usb
```

### オプション説明

| option | 説明 |
|------|------|
| -a | archive |
| -H | hard link |
| -A | ACL |
| -X | extended attributes |
| -x | 別ファイルシステムに跨らない |
| --numeric-ids | UID/GIDを数値で保持 |
| --delete | コピー先の不要ファイルを削除 |

これらを付けないと Linux システムが壊れる可能性がある。

---

# 5. UUID / fstab 更新

USBコピー後、`/etc/fstab` を書き換える必要がある。

UUID取得:

```bash
blkid /dev/sdb2
```

fstab更新例:

```
UUID=USB_ROOT_UUID  /  ext4  defaults,noatime  0 1
```

処理関数:

```
update_fstab()
```

---

# 6. Bootloader 設定

以下の bootloader 関連コマンドは **必ず target root を chroot して実行**する。

## BIOS

```
chroot /mnt/usb grub-install --target=i386-pc /dev/sdb
```

## UEFI

```
chroot /mnt/usb grub-install \
  --target=x86_64-efi \
  --efi-directory=/boot/efi \
  --bootloader-id=oyo
```

## GRUB 設定更新

```
chroot /mnt/usb update-grub
```

---

# 7. initramfs 更新

UUID変更に対応するため initramfs を更新する。

```
chroot /mnt/usb update-initramfs -u
```

---

# 8. machine-id / SSH host key 再生成

コピー直後ではなく、**初回起動（firstboot）で再生成**する。

```
rm -f /etc/machine-id
systemd-machine-id-setup
rm -f /etc/ssh/ssh_host_*
DEBIAN_FRONTEND=noninteractive dpkg-reconfigure openssh-server
```

---

# 9. USB誤操作防止

誤ってシステムディスクを消さないためにチェックを行う。

### root disk 検出

```
findmnt /
```

### デバイス一覧

```
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
```

### removable チェック

```
/sys/block/*/removable
```

USB 以外は選択不可にする。

---

# 10. USB向け最適化

USB I/O を減らすため以下を RAM 化する。

```
/tmp        tmpfs
/var/log    tmpfs
/var/cache  tmpfs
```

---

# 11. Secure Boot スコープ（v1）

v1 の必須サポートは BIOS / UEFI（Secure Boot 無効時）とする。
Secure Boot は **v1.1 以降の限定対応**（Debian系 + shim-signed (Secure Boot対応時のみ) 利用時）として扱う。

限定対応時に必要な前提:

```
shim-signed (Secure Boot対応時のみ)
grub-efi-amd64-signed
```

EFI ディレクトリ例:

```
EFI/
 ├─ BOOT
 └─ oyo
```

---

# 12. CLIモード

GUI だけでなく CLI でも実行できる。

```
oyo-portable-system-cli --device /dev/sdb
```

---

# 13. GUI機能

GUI では以下を提供する。

- USBデバイス選択
- 容量確認
- コピー進捗バー
- ログ表示
- エラー表示

---

# 14. フォルダ構成

```
oyo-portable-system-creator
├── src
│   ├── cli
│   ├── gui
│   ├── lib
│   │    ├── disk.py
│   │    ├── rsync_copy.py
│   │    ├── grub_setup.py
│   │    └── fstab_update.py
│   └── main.py
│
├── debian
│
└── README.md
```

---

# 15. テスト仕様

## 基本テスト

| Test | 内容 |
|-----|------|
| USB作成 | 正常作成 |
| BIOS起動 | 起動確認 |
| UEFI起動 | 起動確認 |
| Secure Boot(任意) | 対応環境での起動確認 |

## 障害テスト

| Test | 内容 |
|-----|------|
| USB抜去 | コピー中にUSBを抜く |
| 電源断 | コピー中に電源断 |
| 容量不足 | 小さいUSB |

---

# 16. 依存パッケージ

```
rsync
parted
dosfstools
e2fsprogs
grub-pc
grub-efi-amd64
shim-signed (Secure Boot対応時のみ)
```

---

# 17. Debian パッケージ

最終成果物:

```
oyo-portable-system-creator.deb
```

インストール後:

```
/usr/bin/oyo-portable-system-creator
```

---

# 18. 将来拡張

- btrfs対応
- snapshot rollback
- encrypted USB
- persistence option

---

# まとめ

本ツールは

**Linuxシステムを安全にUSBへコピーし、ポータブルLinuxを作成するツール**

として設計されている。
