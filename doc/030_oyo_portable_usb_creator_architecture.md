# oyo-portable-system-creator

## 開発アーキテクチャ設計書 v1

本書は **oyo-portable-system-creator**
の実装を行う開発者向けの設計書である。\
詳細機能仕様書 v1 を前提に、以下を定義する。

-   画面遷移仕様
-   内部アーキテクチャ
-   Pythonディレクトリ構成
-   クラス設計
-   処理フローチャート
-   rsync除外リスト

本書では v1 の正本として、**GUI も実行ロジックも Python 実装**を採用する。
外部コマンド（`rsync`, `parted`, `grub-install` など）は Python から `subprocess` で呼び出し、
ログ・例外処理・リトライ方針を共通化する。

------------------------------------------------------------------------

# 1. 画面遷移仕様

本ツールは **ウィザード形式GUI** を採用する。

    Mode Select
       ↓
    Source Check
       ↓
    Target USB Select
       ↓
    Options
       ↓
    Confirm
       ↓
    Execution
       ↓
    Complete

------------------------------------------------------------------------

## 1.1 Mode Select

### 表示

-   「現在のシステムをUSBにコピー」
-   「ポータブルUSBをバックアップ」

### 出力

``` json
{
  "mode": "system_to_usb"
}
```

------------------------------------------------------------------------

## 1.2 Source Check

### 表示情報

  項目          内容
  ------------- ----------------
  OS            open.Yellow.os
  root device   /dev/nvme0n1p2
  home size     12GB
  system size   8GB
  estimated     20GB

### オプション

-   Homeを含める
-   WiFi設定を含める
-   SSH鍵を含める

### 出力

``` json
{
 "source": {
   "root_device": "/dev/nvme0n1p2"
 }
}
```

------------------------------------------------------------------------

## 1.3 Target USB Select

### 表示

USB一覧

  Device     Size   Model
  ---------- ------ ---------------
  /dev/sdb   64GB   SanDisk Ultra

### バリデーション

-   root disk 選択禁止
-   容量不足禁止

------------------------------------------------------------------------

## 1.4 Options

### プリセット

  項目                推奨値
  ------------------- --------
  tmpfs /tmp          ON
  tmpfs /var/tmp      ON
  home cache RAM      ON
  browser cache RAM   ON
  apt cache RAM       OFF

------------------------------------------------------------------------

## 1.5 Confirm

表示内容

-   コピー元
-   コピー先
-   消去警告
-   オプション

チェック

-   データ消去確認

------------------------------------------------------------------------

## 1.6 Execution

表示

-   ステップ進捗
-   ログ

```{=html}
<!-- -->
```
    [STEP] Partitioning
    [STEP] Copying files
    [STEP] Installing bootloader

------------------------------------------------------------------------

## 1.7 Complete

表示

    Portable USB created successfully

------------------------------------------------------------------------

# 2. 内部アーキテクチャ

本ツールは **4層アーキテクチャ** を採用する。

    GUI Layer
        ↓
    Application Layer
        ↓
    System Layer
        ↓
    Utility Layer

------------------------------------------------------------------------

## GUI Layer

役割

-   画面表示
-   ユーザー入力

使用技術

-   PyQt6

------------------------------------------------------------------------

## Application Layer

役割

-   処理フロー制御
-   設定管理

------------------------------------------------------------------------

## System Layer

役割

-   Linux操作
-   rsync
-   partition
-   grub

------------------------------------------------------------------------

## Utility Layer

役割

-   logging
-   validation
-   device detection

------------------------------------------------------------------------

# 3. Pythonディレクトリ構成

    oyo-portable-usb-creator/

    src/

     gui/
       main_window.py
       wizard_pages.py

     core/
       controller.py
       workflow.py

     system/
       device_manager.py
       partition_manager.py
       copy_manager.py
       grub_manager.py

     utils/
       logger.py
       validator.py
       shell.py

     config/
       defaults.py

     main.py

------------------------------------------------------------------------

# 4. クラス設計

## Controller

全体の処理制御

``` python
class Controller:
    def start()
    def analyze_source()
    def select_target()
    def run_copy()
```

------------------------------------------------------------------------

## DeviceManager

USB検出

``` python
class DeviceManager:

    def list_devices()
    def filter_usb()
    def detect_root_disk()
```

------------------------------------------------------------------------

## PartitionManager

パーティション作成

``` python
class PartitionManager:

    def wipe_disk()
    def create_gpt()
    def create_partitions()
```

------------------------------------------------------------------------

## CopyManager

rsyncコピー

``` python
class CopyManager:

    def estimate_size()
    def run_rsync()
```

------------------------------------------------------------------------

## GrubManager

ブート設定

``` python
class GrubManager:

    def install_grub()
    def update_grub()
```

------------------------------------------------------------------------

# 5. 処理フローチャート

    Start
     ↓
    Analyze System
     ↓
    Select Target USB
     ↓
    Safety Check
     ↓
    Partition Disk
     ↓
    Format FS
     ↓
    Mount Target
     ↓
    Rsync Copy
     ↓
    Install GRUB
     ↓
    Apply USB Optimizations
     ↓
    Unmount
     ↓
    Finish

------------------------------------------------------------------------

# 6. rsync除外リスト

rsyncコピー時は以下を除外する。

    /dev/*
    /proc/*
    /sys/*
    /tmp/*
    /run/*
    /mnt/*
    /media/*
    /lost+found

追加推奨

    /var/tmp/*
    /var/cache/apt/*
    /var/lib/apt/lists/*

------------------------------------------------------------------------

# 7. ログ仕様

ログレベル

  level   用途
  ------- ------
  INFO    進捗
  WARN    警告
  ERROR   失敗

------------------------------------------------------------------------

ログ形式

    [STEP] Copy system
    [INFO] rsync started
    [ERROR] rsync failed

------------------------------------------------------------------------

# 8. エラー処理

  エラー        対応
  ------------- ------
  USB容量不足   停止
  rsync失敗     停止
  grub失敗      警告
  mount失敗     停止

------------------------------------------------------------------------

# 9. 将来拡張

v2予定

-   LUKS暗号化
-   Secure Boot
-   btrfs
-   USB修復
-   差分バックアップ
