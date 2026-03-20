# oyo-portable-system-creator

open.Yellow.os 専用の portable USB 作成ツールです（v1）。

## 使い方（CLI）

```bash
python3 -m src.main create --target /dev/sdX --yes --force
python3 -m src.main backup --source /mnt/src-root --target /dev/sdY --yes --force
```

## GUI

```bash
src/bin/oyo-portable-system-creator
```

## 対応範囲
- open.Yellow.os
- ext4 root
- GPT + EFI/FAT32 + root/ext4

## 非対応
- 一般 Debian/Ubuntu
- 復元/修復
- LVM/mdraid/dm-crypt

## テスト

```bash
python3 -m src.main create --help
python3 -m src.main backup --help
python3 -m unittest discover -s tests -v
python3 -m compileall src
```

## Debian パッケージ作成

```bash
sudo apt install build-essential debhelper dh-python python3 python3-pyqt6
dpkg-buildpackage -us -uc -b
```
