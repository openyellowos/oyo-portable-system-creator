# oyo-portable-system-creator フォルダ構成仕様書

## 概要

**oyo-portable-system-creator** は、現在の Linux システムを USB
メモリへコピーし、ポータブル Linux 環境を作成するためのツールである。

主な特徴:

- ISO 書き込みツールではない
- 現在のシステムを USB にコピーしてポータブル Linux を作成
- USB → USB バックアップが可能
- persistence / overlay を使用しない
- 通常の Linux と同じ構成で起動
- USB メモリ向け最適化（tmpfs / cache RAM化など）を適用可能

本ツールは **Debian パッケージ (.deb)** として配布されることを前提とする。

---

# v1 実装方針（正本）

v1 は **GUI も裏側の実行ロジックも Python 実装**とする。

- GUI: PyQt6
- 実行ロジック: Python service / manager クラス
- 外部コマンド実行: Python の `subprocess` 経由で一元管理
- shell スクリプト群は v1 の正本構成には採用しない

この方針により、エラー処理・ログ・テストを Python 側で統一する。

---

# プロジェクト全体構成

```text
oyo-portable-system-creator/
├── LICENSE
├── README.md
├── debian/
│   ├── changelog
│   ├── control
│   ├── copyright
│   ├── install
│   ├── rules
│   └── source/
│       └── format
├── desktop/
│   ├── oyo-portable-system-creator.desktop
│   └── oyo-portable-system-creator.svg
├── packaging/
│   ├── jp.openyellowos.oyo-portable-system-creator.policy
│   └── oyo-portable-system-creator
└── src/
    ├── bin/
    │   ├── oyo-portable-system-creator
    │   └── oyo-portable-system-cli
    ├── gui/
    │   ├── main_window.py
    │   └── wizard_pages.py
    ├── core/
    │   ├── controller.py
    │   ├── workflow.py
    │   └── state.py
    ├── services/
    │   ├── device_service.py
    │   ├── partition_service.py
    │   ├── copy_service.py
    │   ├── boot_service.py
    │   ├── optimize_service.py
    │   └── firstboot_service.py
    ├── infra/
    │   ├── command_runner.py
    │   ├── logger.py
    │   └── chroot.py
    ├── templates/
    │   ├── firstboot.service
    │   ├── tmpfs.conf
    │   ├── fstab.portable
    │   └── grub-portable.cfg
    └── main.py
```

---

# 各ディレクトリの役割

## ルートディレクトリ

| ファイル/ディレクトリ | 説明 |
|---|---|
| LICENSE | ライセンス |
| README.md | プロジェクト概要 |
| debian/ | Debian パッケージング定義 |
| desktop/ | GUI ランチャー |
| packaging/ | polkit / root起動補助 |
| src/ | アプリケーション本体 |

---

## src/bin

CLI / GUI エントリーポイント。

| ファイル | 説明 |
|---|---|
| oyo-portable-system-creator | GUI 起動ラッパー |
| oyo-portable-system-cli | CLI 実行ツール |

---

## src/gui

GUI 実装（PyQt6）。

| ファイル | 説明 |
|---|---|
| main_window.py | メインウィンドウ |
| wizard_pages.py | ウィザード各画面 |

---

## src/core

アプリケーション制御層。

| ファイル | 説明 |
|---|---|
| controller.py | 画面遷移と実行開始制御 |
| workflow.py | 処理オーケストレーション |
| state.py | 実行状態保持 |

---

## src/services

実処理レイヤー（Python）。

| ファイル | 役割 |
|---|---|
| device_service.py | USB / disk 検出と安全判定 |
| partition_service.py | GPT / EFI / root 作成 |
| copy_service.py | rsync 複製 |
| boot_service.py | chroot 下で grub / initramfs 更新 |
| optimize_service.py | tmpfs / journald 最適化 |
| firstboot_service.py | 初回起動処理ファイル配置 |

---

## src/infra

システムコマンド実行・共通基盤。

| ファイル | 説明 |
|---|---|
| command_runner.py | subprocess 実行と標準ログ化 |
| logger.py | 共通ログ出力 |
| chroot.py | mount / chroot / umount 補助 |

---

## src/templates

USB作成時に使用するテンプレートファイル。

| ファイル | 用途 |
|---|---|
| firstboot.service | 初回起動サービス |
| tmpfs.conf | tmpfs設定 |
| fstab.portable | fstabテンプレート |
| grub-portable.cfg | GRUB設定 |

---

# インストール後の配置

```text
/usr/bin/oyo-portable-system-creator
/usr/bin/oyo-portable-system-cli

/usr/lib/oyo-portable-system-creator/
  gui/
  core/
  services/
  infra/
  templates/

/usr/share/applications/
  oyo-portable-system-creator.desktop

/usr/share/icons/hicolor/scalable/apps/
  oyo-portable-system-creator.svg

/usr/share/polkit-1/actions/
  jp.openyellowos.oyo-portable-system-creator.policy
```

---

# 設計方針

- GUI / CLI を分離
- 実行ロジックは Python に統一
- 危険コマンドは `command_runner.py` 経由で集中管理
- Debian パッケージ化を前提
- USBポータブルLinuxを安全に作成可能
