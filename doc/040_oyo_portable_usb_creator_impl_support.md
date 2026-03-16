# oyo-portable-system-creator
## 実装補助設計書 v1
### GUIワイヤーフレーム / 安全設計 / 実装サンプル / systemd設定

本書は、以下の設計・実装を進めるための **実装補助設計書** である。

- GUIワイヤーフレーム
- USB誤選択防止ロジック
- Controller / Workflow 実装サンプル
- `lsblk` パーサ設計
- `rsync` 完全コマンド案
- USB向け最適化用 `systemd` 設定案

---

# 1. 目的

本ツールは **誤ってシステムディスクを消去しないこと** と、  
**USB上でも通常の Linux のように動作すること** が最重要である。

そのため、実装では次の優先順位で設計する。

1. 安全性
2. 再現性
3. ログの明瞭さ
4. GUIの分かりやすさ
5. 拡張性

---

# 2. GUIワイヤーフレーム

本ツールはウィザード形式とし、1画面1目的を徹底する。

---

## 2.1 画面1: モード選択

```text
+------------------------------------------------------+
| oYo Portable USB Creator                             |
+------------------------------------------------------+
| 作成モードを選択してください                         |
|                                                      |
| (●) 現在のシステムをUSBにコピー                      |
| ( ) ポータブルUSBを別のUSBにバックアップ             |
|                                                      |
| 説明:                                                |
| 現在起動中の Linux 環境を、起動可能な USB として     |
| 別の USB デバイスへ複製します。                      |
|                                                      |
|                                  [次へ] [キャンセル] |
+------------------------------------------------------+
```

### 要件
- 初期値は「現在のシステムをUSBにコピー」
- 将来拡張用にモードは enum 管理する
- v1 では未実装モードを選べないようにしてもよい

---

## 2.2 画面2: コピー元確認

```text
+------------------------------------------------------+
| コピー元の確認                                       |
+------------------------------------------------------+
| OS名               : open.Yellow.os                  |
| ルートデバイス     : /dev/nvme0n1p2                  |
| システム使用量     : 18.4 GB                         |
| /home 使用量       : 12.1 GB                         |
| 推定必要容量       : 36 GB                           |
|                                                      |
| 引き継ぐ内容                                        |
| [x] /home を含める                                   |
| [x] Wi-Fi設定を含める                                |
| [ ] SSH秘密鍵を含める                                |
|                                                      |
|                                  [戻る] [次へ]       |
+------------------------------------------------------+
```

### 要件
- 「推定必要容量」は安全率を含めて表示
- SSH秘密鍵は初期値 OFF
- root デバイスは読み取り専用表示

---

## 2.3 画面3: コピー先USB選択

```text
+------------------------------------------------------------------+
| コピー先USBの選択                                                |
+------------------------------------------------------------------+
| 利用可能なUSBデバイス                                            |
|                                                                  |
| [ ] /dev/sdb   64GB   SanDisk Ultra USB 3.0     未マウント       |
| [ ] /dev/sdc  128GB   BUFFALO USB Flash Disk    一部マウント     |
|                                                                  |
| [再読み込み]                                                     |
|                                                                  |
| 注意: 選択したUSBの内容はすべて消去されます。                    |
|                                                                  |
|                                        [戻る] [次へ]             |
+------------------------------------------------------------------+
```

### 要件
- root disk は表示しない
- 内蔵ディスクは初期状態では表示しない
- 容量不足デバイスは選択不可、理由を注記する
- 一部マウント中なら警告表示

---

## 2.4 画面4: オプション設定

```text
+------------------------------------------------------+
| オプション設定                                       |
+------------------------------------------------------+
| プリセット: [ 推奨設定 ▼ ]                           |
|                                                      |
| USB向け最適化                                        |
| [x] /tmp をRAM化                                     |
| [x] /var/tmp をRAM化                                 |
| [x] ユーザーキャッシュをRAM化                        |
| [x] ブラウザキャッシュをRAM化                        |
| [ ] APTキャッシュをRAM化                             |
| [x] journald を volatile にする                      |
|                                                      |
| 再生成設定                                            |
| [x] machine-id を再生成                              |
| [x] SSH host key を再生成                            |
|                                                      |
|                                  [戻る] [次へ]       |
+------------------------------------------------------+
```

### 要件
- v1 は「推奨設定」と「詳細カスタム」の二択に絞る
- APTキャッシュRAM化は初期値 OFF
- GNOME Software 安定性優先の説明を表示可能にする

---

## 2.5 画面5: 最終確認

```text
+------------------------------------------------------+
| 最終確認                                             |
+------------------------------------------------------+
| コピー元 : 現在起動中のシステム                      |
| コピー先 : /dev/sdb (64GB / SanDisk Ultra)           |
| 構成     : EFI 512MB + root ext4                     |
|                                                      |
| 引き継ぐ内容                                         |
|  - /home                                             |
|  - Wi-Fi設定                                         |
|                                                      |
| 引き継がない内容                                     |
|  - SSH秘密鍵                                         |
|                                                      |
| [x] コピー先USBの内容が全て消えることを理解しました   |
| [x] コピー元とコピー先を確認しました                 |
|                                                      |
|                                  [戻る] [実行]       |
+------------------------------------------------------+
```

### 要件
- 実行ボタンはチェック2つが両方ONで有効
- 実行前に確認ダイアログをもう1回出してもよい

---

## 2.6 画面6: 実行中

```text
+------------------------------------------------------+
| 実行中                                               |
+------------------------------------------------------+
| 現在のステップ: ファイルコピー中                     |
|                                                      |
| [1/6] 安全確認            完了                       |
| [2/6] パーティション作成  完了                       |
| [3/6] フォーマット        完了                       |
| [4/6] ファイルコピー      実行中                     |
| [5/6] GRUB設定            待機                       |
| [6/6] 最適化設定          待機                       |
|                                                      |
| ログ                                                 |
| --------------------------------------------------   |
| [INFO] rootfs copy started                           |
| [INFO] rsync --aHAX ...                              |
|                                                      |
|                         [詳細ログ] [中止(危険)]      |
+------------------------------------------------------+
```

### 要件
- 中止は「安全に止められる段階」だけ許可
- 破壊的処理中は中止ボタンを無効化
- 詳細ログを別ダイアログで表示可能にする

---

## 2.7 画面7: 完了

```text
+------------------------------------------------------+
| 完了                                                 |
+------------------------------------------------------+
| Portable USB の作成が完了しました。                  |
|                                                      |
| コピー先: /dev/sdb                                   |
|                                                      |
| 次にできること                                       |
|  - USBから起動して動作確認する                       |
|  - BIOS/UEFI の起動順を変更する                      |
|                                                      |
| [ログを保存]                         [閉じる]        |
+------------------------------------------------------+
```

---

# 3. USB誤選択防止ロジック

本ツールで最重要なのは **消してはいけないディスクを絶対に消さないこと** である。

---

## 3.1 判定対象

以下を取得する。

- 現在の `/` のマウント元
- `/boot`
- `/boot/efi`
- `/home`
- swap 使用デバイス
- LVM / mdraid / dm-crypt の親子関係
- `lsblk` の `pkname`
- `findmnt`
- `/proc/mounts`

---

## 3.2 選択禁止条件

以下に1つでも該当するディスクは選択禁止とする。

1. 現在の root filesystem が存在するディスク
2. `/boot` が存在するディスク
3. `/boot/efi` が存在するディスク
4. `/home` が存在するディスク
5. swap が存在するディスク
6. それらの親ディスク
7. `TRAN != usb` のディスク（v1）
8. `RO=1` のディスク
9. サイズ取得不能のディスク

---

## 3.3 判定アルゴリズム

### 手順

1. `findmnt -no SOURCE /`
2. `findmnt -no SOURCE /boot`
3. `findmnt -no SOURCE /boot/efi`
4. `findmnt -no SOURCE /home`
5. `swapon --show --noheadings --raw`
6. 取得したデバイスを「禁止デバイス集合」に追加
7. `lsblk -J` で親ディスクへ遡る
8. 親ディスクも禁止集合へ追加
9. GUI表示対象から除外

---

## 3.4 Python擬似コード

```python
def build_forbidden_disks(lsblk_info, mount_sources, swap_sources):
    forbidden = set()

    for dev in mount_sources + swap_sources:
        if not dev:
            continue
        forbidden.add(dev)
        parent = find_parent_disk(lsblk_info, dev)
        if parent:
            forbidden.add(parent)

    return forbidden
```

---

## 3.5 追加安全策

- 容量が極端に大きい内蔵NVMeは既定で非表示
- モデル名に `Samsung`, `WDC`, `KIOXIA`, `NVMe`, `SSD` が含まれても、それだけで内蔵判定しない
- ただし `TRAN=nvme/sata/ata` は v1 では対象外
- GUIに「このUSBは完全に消去されます」を常時表示

---

# 4. `lsblk` パーサ設計

---

## 4.1 使用コマンド

```bash
lsblk -J -b -o NAME,KNAME,PATH,PKNAME,SIZE,MODEL,SERIAL,TRAN,RM,RO,TYPE,FSTYPE,MOUNTPOINTS
```

---

## 4.2 期待JSON例

```json
{
  "blockdevices": [
    {
      "name": "sdb",
      "kname": "sdb",
      "path": "/dev/sdb",
      "pkname": null,
      "size": 64023257088,
      "model": "SanDisk Ultra",
      "serial": "123456",
      "tran": "usb",
      "rm": true,
      "ro": false,
      "type": "disk",
      "fstype": null,
      "mountpoints": [null],
      "children": [
        {
          "name": "sdb1",
          "path": "/dev/sdb1",
          "pkname": "sdb",
          "type": "part",
          "fstype": "vfat",
          "mountpoints": ["/media/live/EFI"]
        }
      ]
    }
  ]
}
```

---

## 4.3 内部データモデル

```python
from dataclasses import dataclass, field

@dataclass
class BlockDevice:
    name: str
    path: str
    size_bytes: int
    model: str | None
    serial: str | None
    tran: str | None
    rm: bool
    ro: bool
    dev_type: str
    fstype: str | None
    mountpoints: list[str]
    pkname: str | None = None
    children: list["BlockDevice"] = field(default_factory=list)
```

---

## 4.4 パース時の注意点

- `mountpoints` は `None` を含む場合がある
- `rm` / `ro` は 0/1 で来ることがある
- `children` が存在しない場合がある
- `size` は文字列で返る環境もあるため int 化時に防御する
- `tran` が `null` の USB ブリッジ機器がある可能性がある

---

## 4.5 表示候補ディスクの判定

```python
def is_candidate_usb_disk(dev: BlockDevice) -> bool:
    return (
        dev.dev_type == "disk"
        and not dev.ro
        and (dev.tran == "usb" or dev.rm)
    )
```

※ v1 では `rm` だけで許可すると誤判定の可能性があるため、  
実装時は `tran == "usb"` を優先し、`rm` は補助情報とする。

---

# 5. Controller / Workflow 実装サンプル

---

## 5.1 役割分担

- `Controller`: GUI からの入力と遷移制御
- `Workflow`: 実処理のオーケストレーション
- `DeviceManager`: デバイス取得と安全判定
- `PartitionManager`: パーティション操作
- `CopyManager`: rsync 複製
- `BootManager`: GRUB設定
- `OptimizationManager`: USB最適化設定適用

---

## 5.2 `Controller` サンプル

```python
class Controller:
    def __init__(self, workflow, state, logger):
        self.workflow = workflow
        self.state = state
        self.logger = logger

    def load_source_info(self):
        self.state.source_info = self.workflow.analyze_source()
        return self.state.source_info

    def load_target_devices(self):
        self.state.target_candidates = self.workflow.list_target_devices()
        return self.state.target_candidates

    def validate_before_run(self):
        return self.workflow.validate_plan(self.state)

    def execute(self, progress_callback):
        return self.workflow.run(self.state, progress_callback)
```

---

## 5.3 `Workflow` サンプル

```python
class Workflow:
    def __init__(
        self,
        device_manager,
        partition_manager,
        copy_manager,
        boot_manager,
        optimization_manager,
        logger,
    ):
        self.device_manager = device_manager
        self.partition_manager = partition_manager
        self.copy_manager = copy_manager
        self.boot_manager = boot_manager
        self.optimization_manager = optimization_manager
        self.logger = logger

    def analyze_source(self):
        return self.copy_manager.analyze_running_system()

    def list_target_devices(self):
        return self.device_manager.list_safe_target_devices()

    def validate_plan(self, state):
        self.device_manager.assert_target_allowed(state.target_device)
        self.copy_manager.assert_capacity_ok(
            state.source_info.estimated_required_bytes,
            state.target_device.size_bytes,
        )
        return True

    def run(self, state, progress_callback):
        progress_callback("safety_check", "running")
        self.validate_plan(state)
        progress_callback("safety_check", "done")

        progress_callback("partition", "running")
        self.partition_manager.prepare_disk(state.target_device.path)
        progress_callback("partition", "done")

        progress_callback("copy", "running")
        mount_info = self.copy_manager.copy_rootfs(state)
        progress_callback("copy", "done")

        progress_callback("boot", "running")
        self.boot_manager.install_bootloader(mount_info)
        progress_callback("boot", "done")

        progress_callback("optimize", "running")
        self.optimization_manager.apply(state, mount_info)
        progress_callback("optimize", "done")

        progress_callback("finalize", "running")
        self.boot_manager.finalize(mount_info)
        progress_callback("finalize", "done")

        return True
```

---

## 5.4 状態保持用データモデル

```python
from dataclasses import dataclass

@dataclass
class AppState:
    mode: str = "system_to_usb"
    source_info: object | None = None
    target_candidates: list | None = None
    target_device: object | None = None
    options: dict | None = None
```

---

# 6. `rsync` 完全コマンド案

v1 では基本的に `rsync` によるフルコピーを採用する。

---

## 6.1 推奨コマンド

```bash
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
  / /mnt/target
```

---

## 6.2 オプションの意味

|オプション|意味|
|---|---|
|`-a`|権限・時刻などを保持|
|`-H`|ハードリンク保持|
|`-A`|ACL保持|
|`-X`|xattr保持|
|`-x`|別ファイルシステムへ跨がない|
|`--numeric-ids`|UID/GIDを数値で保持|
|`--delete`|コピー先不要ファイル削除|
|`--info=progress2`|進捗表示|

---

## 6.3 追加候補

必要に応じて以下も検討できる。

```bash
--exclude=/var/cache/apt/archives/*
--exclude=/var/lib/apt/lists/*
--exclude=/home/*/.cache/*
```

ただし、`.cache` をコピーしない場合は、GUIオプションと整合させること。

---

## 6.4 注意点

- `/boot/efi` をコピーするだけでは不十分で、別途 GRUB 再導入が必要
- `-x` を付けることで bind mount 等への誤コピーを減らせる
- `--delete` はコピー先既存内容を消すため、空の新規 rootfs 前提が望ましい

---

# 7. USB最適化の `systemd` 設定案

---

## 7.1 `/tmp` と `/var/tmp` の tmpfs

### `/etc/fstab` 例

```fstab
tmpfs /tmp     tmpfs defaults,noatime,mode=1777 0 0
tmpfs /var/tmp tmpfs defaults,noatime,mode=1777 0 0
```

---

## 7.2 journald volatile

### `/etc/systemd/journald.conf.d/portable.conf`

```ini
[Journal]
Storage=volatile
RuntimeMaxUse=128M
SystemMaxUse=0
```

---

## 7.3 ユーザーキャッシュRAM化

### 方針
- ログイン時に `/run/user/$UID/oyo-cache` を作成
- `~/.cache` をシンボリックリンク化

### 初回起動スクリプト例

```bash
#!/bin/sh
set -eu

TARGET="/run/user/$(id -u)/oyo-cache"
mkdir -p "$TARGET"

if [ -e "$HOME/.cache" ] && [ ! -L "$HOME/.cache" ]; then
    rm -rf "$HOME/.cache"
fi

ln -sfn "$TARGET" "$HOME/.cache"
```

---

## 7.4 Chrome / Chromium / Firefox キャッシュ

### 方針
- プロファイル本体は永続
- キャッシュだけ RAM へ逃がす

### 例
- `~/.config/google-chrome/Cache` → `/run/user/$UID/oyo-google-chrome/Cache`
- `~/.cache/mozilla/firefox` はそのまま `~/.cache` RAM化の恩恵を受ける

---

## 7.5 APTキャッシュ

v1 では初期値 OFF とする。  
理由は以下の通り。

- GUIパッケージ管理との相性差がある
- RAM使用量が増える
- USB寿命より安定性を優先したい

APTキャッシュRAM化を使う場合は次のような mount を使う。

```fstab
tmpfs /var/cache/apt/archives tmpfs defaults,noatime,mode=0755,size=512M 0 0
```

---

## 7.6 machine-id 再生成

### 方針
コピー直後ではなく、初回起動時に再生成する。

### 例

```bash
rm -f /etc/machine-id
systemd-machine-id-setup
```

---

## 7.7 SSH host key 再生成

### 方針
コピー元と同一鍵を持たないよう、初回起動で再生成する。

```bash
rm -f /etc/ssh/ssh_host_*
dpkg-reconfigure openssh-server
```

---

# 8. first boot 設計

初回起動でだけ実行する処理は、1回限りの service と script にまとめる。

---

## 8.1 推奨構成

- `/usr/local/lib/oyo/portable-firstboot.sh`
- `/etc/systemd/system/oyo-portable-firstboot.service`

---

## 8.2 service 例

```ini
[Unit]
Description=oYo Portable first boot initialization
After=multi-user.target
ConditionPathExists=!/var/lib/oyo-portable/firstboot.done

[Service]
Type=oneshot
ExecStart=/usr/local/lib/oyo/portable-firstboot.sh

[Install]
WantedBy=multi-user.target
```

---

## 8.3 script 例

```bash
#!/bin/sh
set -eu

mkdir -p /var/lib/oyo-portable

rm -f /etc/machine-id
systemd-machine-id-setup

if command -v dpkg-reconfigure >/dev/null 2>&1; then
    rm -f /etc/ssh/ssh_host_*
    DEBIAN_FRONTEND=noninteractive dpkg-reconfigure openssh-server || true
fi

touch /var/lib/oyo-portable/firstboot.done
```

---

# 9. ログ設計補強

---

## 9.1 ログ出力先

- GUI簡易ログ: 画面表示用メモリバッファ
- 詳細ログ: `/tmp/oyo-portable-creator.log`
- 失敗時サマリ: `/tmp/oyo-portable-creator.error.log`

---

## 9.2 推奨ログ形式

```text
2026-03-16 18:40:12 [STEP] safety_check
2026-03-16 18:40:12 [INFO] target=/dev/sdb
2026-03-16 18:40:13 [STEP] partition
2026-03-16 18:40:20 [INFO] mkfs.ext4 completed
2026-03-16 18:41:10 [ERROR] grub-install failed: exit=1
```

---

## 9.3 ログ要件

- 実行コマンドは必ず残す
- 標準出力と標準エラーを区別できると望ましい
- GUIには要約だけ表示し、詳細は展開表示にする
- 完了時にログ保存ボタンを提供する

---

# 10. 実装優先順位

v1 の開発は以下の順序を推奨する。

1. `lsblk` パーサ
2. USB誤選択防止ロジック
3. パーティション作成
4. mount / rsync 複製
5. chroot / GRUB 設定
6. firstboot 処理
7. GUI統合
8. ログ保存

---

# 11. v1 完了条件

以下を満たしたら v1 完了とみなす。

- 現在起動中の **open.Yellow.os** を USB に複製できる
- 作成した USB が BIOS / UEFI の少なくとも基本構成で起動できる
- root disk を誤って選べない
- `/tmp` / `~/.cache` / journald の最適化が有効
- 初回起動で machine-id / SSH host key を再生成できる
- GUI から最後まで操作できる
- エラー時に調査可能なログが残る

---

# 12. 今後の次段階候補

本書の次に作るとよい文書は以下である。

1. **テスト仕様書**
2. **GUI部品一覧**
3. **エラーコード一覧**
4. **インストール後生成ファイル一覧**
5. **Python雛形コード一式**

---

# 13. 実装前レビュー指摘の反映（不足項目）

本節は、v1 実装開始前に指摘された「仕様にあるが実装条件として弱い点」を
開発タスク化するためのチェックリストである。

## 13.1 スコープ固定（v1）

- 対応OSは **open.Yellow.os のみ** とする
- コピー元OS判定は `E120` を返せること（`/etc/os-release` 判定）
- GUI の説明文も「Debian系一般対応」のような曖昧表現を避ける

## 13.2 事前検証で不足している実装項目

### A. OS/環境チェック
- `ID=openyellowos` または `NAME=open.Yellow.os` を満たさない場合は開始不可
- root 権限必須チェック（不足時は `E121` 相当で停止）
- 必須コマンド存在チェック（`rsync`, `parted`, `mkfs.*`, `grub-install`, `update-initramfs`）

### B. コピー元構成チェック
- root が `ext4` 以外の場合の挙動を明文化（v1 は非対応停止）
- `LVM` / `mdraid` / `dm-crypt` 検出を実コードに落とす
- `/boot`・`/boot/efi` の有無を前提分岐として保持

### C. 容量見積りチェック
- `required_bytes = used_bytes("/") * 1.15 + 4GiB` を実装する
- 見積り根拠（使用量・安全率・固定バッファ）を GUI/CLI ログに出す

## 13.3 実行処理で不足している実装項目

### A. マウント/アンマウント安全化
- 途中失敗時にも `sync` → `umount`（必要なら lazy umount）を必ず実行
- chroot bind mount（`/dev`, `/proc`, `/sys`）の解除順序を固定

### B. GRUB / initramfs
- BIOS/UEFI 両方の install を対象環境に応じて実行
- `update-grub` 失敗時は致命扱い（v1）
- `update-initramfs -u` を finalization に必ず含める

### C. firstboot 配置
- `firstboot.service` 配置と `enable` の成否を検証する
- `firstboot.done` の作成パスを仕様と一致させる（`/var/lib/oyo-portable/firstboot.done`）

## 13.4 ログ・エラー仕様で不足している実装項目

- エラーコードと CLI 終了コードの対応を共通関数化する
- GUI は「ユーザー向け文言」、詳細ログは「原因コマンド + exit code」を残す
- 機密オプション（Wi-Fi/SSH鍵）の ON/OFF は記録し、秘密値は記録しない

## 13.5 テスト観点で不足している項目（最小）

- open.Yellow.os 以外で `E120` になること
- root disk が候補に出ないこと
- 容量不足で `E202` になること
- rsync 失敗で `E401` になること
- grub 失敗で `E501` になること
- firstboot 準備失敗で `E601` になること

## 13.6 実装着手ゲート（DoR）

以下を満たすまで、実装を「着手可」としない。

1. v1 スコープが open.Yellow.os のみで文書間一致している
2. エラーコードと停止条件が仕様書と一致している
3. 安全停止（cleanup）手順が疑似コードレベルで定義済み
4. テスト仕様（異常系含む）に最低ケースが記載済み
