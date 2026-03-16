
# oyo-portable-system-creator 仕様書 v2

作成日: 2026-03-16

---

# 1. 概要

**oyo-portable-system-creator** は、既存の Linux システム（open.Yellow.os）を
USB メモリへコピーし、**ポータブル Linux 環境**を作成するツールである。

特徴:

- 現在の Linux 環境をそのまま USB にコピー
- BIOS / UEFI 両対応
- BIOS / UEFI は v1 必須（Secure Boot は v1.1 以降の限定対応）
- rsync ベースの安全なコピー
- GUI / CLI 両対応
- USB 向けパフォーマンス最適化

v1 対応OSスコープ:

- コピー元（実行元）OS: **open.Yellow.os のみ対応**
- コピー先（作成USB）OS: **open.Yellow.os のみ対応**
- Debian / Ubuntu 系一般対応は v1 の対象外（将来対応）

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

v1 の固定ルール:

- パーティション開始位置は `1MiB` とし、アラインメントずれを防止する
- EFI は常に 512MiB 固定（`1MiB-513MiB`）
- 最低必要容量は次式で判定する

```
required_bytes = used_bytes("/") * 1.15 + 4GiB
```

- `target_bytes < required_bytes` の場合は処理開始前に停止する

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
  --exclude=/swapfile \
  --exclude=/var/lib/oyo-portable/firstboot.done \
  / /mnt/usb
```

`--delete` は v1 では **常時有効**（再実行時の不整合防止を優先）とする。

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
oyo-portable-system-cli create --device /dev/sdb
```

CLI引数仕様（v1確定）:

| 引数 | 必須 | 型 | 既定値 | 説明 |
|---|---|---|---|---|
| `create` | Yes | subcommand | - | USB 作成モード |
| `--device` | Yes | path | - | 例: `/dev/sdb` |
| `--include-wifi` | No | flag | OFF | NetworkManager 接続設定をコピー |
| `--include-ssh-keys` | No | flag | OFF | `~/.ssh` をコピー |
| `--optimize` | No | enum(`recommended`,`off`) | `recommended` | USB最適化の適用有無 |
| `--dry-run` | No | flag | OFF | 破壊的処理前までを検証実行 |
| `--log-file` | No | path | `/tmp/oyo-portable-creator.log` | ログ出力先 |

終了コード（v1確定）:

| code | 意味 |
|---|---|
| `0` | 成功 |
| `2` | 引数エラー |
| `3` | 環境非対応（OS/権限/依存不足） |
| `4` | デバイス検証エラー |
| `5` | 実行中エラー（partition/rsync/grub など） |

---

# 13. GUI機能

GUI では以下を提供する。

- USBデバイス選択
- 容量確認
- コピー進捗バー
- ログ表示
- エラー表示

GUIオプション既定値（v1確定）:

| UI項目ID | 既定値 | 制約 |
|---|---|---|
| `opt.include_wifi` | ON | root 権限時のみ有効 |
| `opt.include_ssh_keys` | OFF | ON時は確認ダイアログ必須 |
| `opt.optimize_mode` | `recommended` | `recommended/off` のみ |
| `opt.dry_run` | OFF | ON時は partition 直前で停止 |

---

# 14. フォルダ構成

```
oyo-portable-system-creator
├── src
│   ├── bin
│   │   ├── oyo-portable-system-creator
│   │   └── oyo-portable-system-cli
│   ├── gui
│   │   ├── main_window.py
│   │   └── wizard_pages.py
│   ├── core
│   │   ├── controller.py
│   │   ├── workflow.py
│   │   └── state.py
│   ├── services
│   │   ├── device_service.py
│   │   ├── partition_service.py
│   │   ├── copy_service.py
│   │   ├── boot_service.py
│   │   ├── optimize_service.py
│   │   └── firstboot_service.py
│   ├── infra
│   │   ├── command_runner.py
│   │   ├── logger.py
│   │   └── chroot.py
│   └── templates
│       ├── firstboot.service
│       ├── fstab.portable
│       ├── tmpfs.conf
│       └── grub-portable.cfg
│   └── main.py
│
├── debian
│
└── README.md
```

v1 の正本構成は上記とし、`src/lib` / `src/system` / `src/utils` などの旧記述は参照しない。

---

# 15. テスト仕様

## 基本テスト

| Test | 内容 |
|-----|------|
| USB作成 | 正常作成 |
| BIOS起動 | 起動確認 |
| UEFI起動 | 起動確認 |
| Secure Boot(任意) | 対応環境での起動確認 |

受け入れ基準（v1）:

- 20GB の実データを 32GB USB(USB3.0 相当)へコピーした場合、完了目安は 120 分以内
- 実行中のアプリ追加メモリ使用量（常時）は 512MB 以下
- 詳細ログは 1 実行あたり 50MB 以下を目安とし、超過時はローテートする

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

- Debian / Ubuntu 系への一般対応
- btrfs対応
- snapshot rollback
- encrypted USB
- persistence option

---

# 19. サポートマトリクス（v1）

| 項目 | v1 方針 |
|---|---|
| コピー元OS | open.Yellow.os のみ対応 |
| 作成先USB OS | open.Yellow.os のみ対応 |
| root on ext4 (単一パーティション) | 対応 |
| root on LVM | 非対応（検出したら実行拒否） |
| root on mdraid | 非対応（検出したら実行拒否） |
| root on dm-crypt/LUKS | 非対応（検出したら実行拒否） |
| BIOS 起動 | 対応 |
| UEFI 起動 | 対応 |
| Secure Boot | 必須外（v1.1 以降） |

非対応構成を検出した場合は、GUI/CLI ともに理由付きで停止し、破壊的処理へ進まない。

OS判定ルール（v1）:

1. `/etc/os-release` を読み取り、`NAME="open.Yellow.os"` または `ID=openyellowos` を許可
2. 条件不一致なら `E120` として停止
3. `--dry-run` でも OS 判定は必須

---

# 20. 失敗時ポリシー（ロールバック/クリーンアップ）

| ステップ | 失敗時動作 | クリーンアップ | 再実行 |
|---|---|---|---|
| デバイス検証 | 即停止 | なし | 条件修正後に可 |
| パーティション作成 | 即停止 | mount があれば解除 | 可（再初期化前提） |
| フォーマット | 即停止 | mount があれば解除 | 可 |
| rsync | 即停止 | `/mnt/usb` を安全に umount | 可（先頭から再実行） |
| grub-install/update-grub | 即停止 | chroot/mount を解除 | 可 |
| 最適化適用 | 警告または停止（設定次第） | 可能な範囲で差分戻し | 可 |

共通ルール:

- 失敗時は必ず `sync` 実行後に umount を試行する
- 「未完成USB」であることをログと完了画面（失敗画面）に明示する
- `--force-resume` は v1 では提供せず、再実行はフル再実行のみ

---

# 21. firstboot責務分離（build時との境界）

build時に実施:

- rootfs コピー
- fstab 生成
- firstboot service/script 配置
- journald/tmpfs など静的設定ファイル配置

firstbootで実施:

- `/etc/machine-id` 再生成
- SSH host key 再生成
- ユーザーキャッシュのRAM化（必要時）
- 完了マーカー作成（例: `/var/lib/oyo-portable/firstboot.done`）

firstboot 状態遷移（v1確定）:

| 状態 | 条件 | 次状態 |
|---|---|---|
| `pending` | build時に service 配置済み | `running` |
| `running` | machine-id / SSH key 再生成実行中 | `done` or `retryable_failed` |
| `retryable_failed` | 一時失敗（例: I/O 一過性） | 次回起動で `running` 再試行 |
| `done` | すべて成功、`firstboot.done` 作成 | 終了 |

更新順序:

1. machine-id 再生成
2. SSH host key 再生成
3. 必要な最適化処理
4. `firstboot.done` 作成
5. service 自己無効化

---

# 22. 機密データコピー仕様（v1）

オプション別コピー対象（例）:

- Wi-Fi設定を含める: `/etc/NetworkManager/system-connections/*`
- SSH鍵を含める（既定OFF）: `~/.ssh/*`

安全要件:

- GUI は「機密情報を含む可能性」を明示し、既定値は安全側（SSH鍵 OFF）
- ログには秘密情報そのものを出力しない（ファイル内容のダンプ禁止）
- 実行記録には「どの機密オプションが ON だったか」だけを残す

---

# 23. エラーコード仕様（v1）

| コード | 代表トリガ | GUI表示の要点 | CLI終了コード |
|---|---|---|---|
| `E120` | 対応OS外 | 「v1はopen.Yellow.osのみ対応」 | `3` |
| `E201` | root disk / 非USB選択 | 「対象デバイスを選択できません」 | `4` |
| `E202` | 容量不足 | 「容量不足です」 | `4` |
| `E301` | パーティション作成失敗 | 「ディスク初期化に失敗」 | `5` |
| `E302` | フォーマット失敗 | 「ファイルシステム作成に失敗」 | `5` |
| `E401` | rsync失敗 | 「コピー中に失敗」 | `5` |
| `E501` | grub/update-grub失敗 | 「起動設定に失敗」 | `5` |
| `E601` | firstboot準備失敗 | 「初回起動設定に失敗」 | `5` |

---

# まとめ

本ツールは

**Linuxシステムを安全にUSBへコピーし、ポータブルLinuxを作成するツール**

として設計されている。
