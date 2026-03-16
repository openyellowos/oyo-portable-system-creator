# oyo-portable-system-creator フォルダ構成仕様書

## 概要

**oyo-portable-system-creator** は、現在の Linux システムを USB
メモリへコピーし、 ポータブル Linux 環境を作成するためのツールである。

主な特徴:

-   ISO 書き込みツールではない
-   現在のシステムを USB にコピーしてポータブル Linux を作成
-   USB → USB バックアップが可能
-   persistence / overlay を使用しない
-   通常の Linux と同じ構成で起動
-   USB メモリ向け最適化（tmpfs / cache RAM化など）を適用可能

本ツールは **Debian パッケージ (.deb)**
として配布されることを前提とする。

------------------------------------------------------------------------

# プロジェクト全体構成

    oyo-portable-system-creator/
    ├── LICENSE
    ├── README.md
    ├── debian
    │   ├── changelog
    │   ├── control
    │   ├── copyright
    │   ├── install
    │   ├── rules
    │   └── source
    │       └── format
    ├── desktop
    │   ├── oyo-portable-system-creator.desktop
    │   └── oyo-portable-system-creator.svg
    ├── packaging
    │   ├── jp.openyellowos.oyo-portable-system-creator.policy
    │   └── oyo-portable-system-creator
    └── src
        ├── bin
        │   ├── oyo-portable-system-creator
        │   └── oyo-portable-system-cli
        ├── gui
        │   └── oyo_portable_system_gui.py
        ├── lib
        │   ├── backup.sh
        │   ├── bootloader.sh
        │   ├── cleanup.sh
        │   ├── common.sh
        │   ├── copy_system.sh
        │   ├── detect.sh
        │   ├── disk.sh
        │   ├── finalize.sh
        │   ├── firstboot.sh
        │   ├── fstab.sh
        │   ├── optimize.sh
        │   ├── partition.sh
        │   ├── validate.sh
        │   └── wipe.sh
        └── templates
            ├── firstboot.service
            ├── tmpfs.conf
            ├── fstab.portable
            └── grub-portable.cfg

------------------------------------------------------------------------

# 各ディレクトリの役割

## ルートディレクトリ

  ファイル     説明
  ------------ ---------------------------
  LICENSE      ライセンス
  README.md    プロジェクト概要
  debian/      Debian パッケージング定義
  desktop/     GUI ランチャー
  packaging/   polkit / root起動補助
  src/         アプリケーション本体

------------------------------------------------------------------------

# src ディレクトリ

アプリケーション本体を格納するディレクトリ。

    src/
    ├── bin
    ├── gui
    ├── lib
    └── templates

------------------------------------------------------------------------

# src/bin

CLI エントリーポイント。

  ファイル                      説明
  ----------------------------- ------------------
  oyo-portable-system-creator   GUI 起動ラッパー
  oyo-portable-system-cli       CLI 実行ツール

------------------------------------------------------------------------

# src/gui

GUI 実装。

  ファイル                     説明
  ---------------------------- -----------
  oyo_portable_system_gui.py   メインGUI

GUIは以下機能を提供:

-   USB デバイス選択
-   作成モード選択
-   USB 作成
-   USB バックアップ
-   USB 最適化
-   進行ログ表示

------------------------------------------------------------------------

# src/lib

実処理を行うシェルスクリプト群。

  スクリプト       役割
  ---------------- ------------------
  common.sh        共通関数
  detect.sh        USB / disk 検出
  disk.sh          ディスク情報取得
  validate.sh      入力チェック
  wipe.sh          既存データ削除
  partition.sh     GPT / EFI 作成
  copy_system.sh   システムコピー
  backup.sh        USBバックアップ
  bootloader.sh    GRUBインストール
  fstab.sh         fstab生成
  optimize.sh      tmpfs最適化
  firstboot.sh     初回起動処理
  cleanup.sh       後処理
  finalize.sh      最終処理

------------------------------------------------------------------------

# src/templates

USB作成時に使用するテンプレートファイル。

  ファイル            用途
  ------------------- -------------------
  firstboot.service   初回起動サービス
  tmpfs.conf          tmpfs設定
  fstab.portable      fstabテンプレート
  grub-portable.cfg   GRUB設定

------------------------------------------------------------------------

# desktop

デスクトップ統合用。

  ファイル                              説明
  ------------------------------------- ------------------
  oyo-portable-system-creator.desktop   アプリランチャー
  oyo-portable-system-creator.svg       アイコン

------------------------------------------------------------------------

# packaging

root権限処理関連。

  ファイル                                             説明
  ---------------------------------------------------- ------------------
  jp.openyellowos.oyo-portable-system-creator.policy   polkit
  oyo-portable-system-creator                          root起動ラッパー

------------------------------------------------------------------------

# debian

Debian パッケージ作成定義。

  ファイル        説明
  --------------- ----------------
  control         パッケージ情報
  rules           ビルドルール
  install         ファイル配置
  changelog       変更履歴
  copyright       著作権
  source/format   ソース形式

------------------------------------------------------------------------

# インストール後の配置

    /usr/bin/oyo-portable-system-creator

    /usr/lib/oyo-portable-system-creator/
        gui/
        lib/
        templates/

    /usr/share/applications/
        oyo-portable-system-creator.desktop

    /usr/share/icons/hicolor/scalable/apps/
        oyo-portable-system-creator.svg

    /usr/share/polkit-1/actions/
        jp.openyellowos.oyo-portable-system-creator.policy

------------------------------------------------------------------------

# 設計方針

このプロジェクトは以下の方針で設計する。

-   GUI / CLI を分離
-   シェルスクリプトをモジュール化
-   Debianパッケージ化を前提
-   USBポータブルLinuxを簡単に作成可能
