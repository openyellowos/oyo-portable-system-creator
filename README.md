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

## リリース手順

GitHub Actions により、`v*` 形式のタグを push すると自動で `.deb` をビルドし、GitHub Release に生成物を添付します。

まず `debian/changelog` の先頭エントリを次のリリース版に更新します。Git タグは Debian パッケージ版の upstream version に合わせて、たとえば `0.1.0-1` に対して `v0.1.0` を使います。

`debian/changelog` は手動編集でも更新できますが、Debian では `dch` コマンドを使うのが一般的です。`dch` は `devscripts` パッケージに含まれます。

```bash
sudo apt install devscripts
```

Debian revision だけを 1 つ上げる場合:

```bash
dch -i
```

upstream version を指定して新しいリリースを作る場合:

```bash
dch -v 0.1.0-1
```

その後、生成された changelog エントリの本文を編集します。

```debchanges
oyo-portable-system-creator (0.1.0-1) unstable; urgency=medium

  * Release notes here.

 -- OYO Team <devnull@example.com>  Thu, 20 Mar 2026 00:00:00 +0000
```

```bash
git add debian/changelog
git commit -m "Release 0.1.0"
git tag v0.1.0
git push origin v0.1.0
```

実行されるワークフロー:
- `.github/workflows/build-release.yml`
- `dpkg-buildpackage -us -uc` で Debian パッケージをビルド
- `.deb`, `.changes`, `.buildinfo`, `.dsc`, `.tar.*` を Release に添付
