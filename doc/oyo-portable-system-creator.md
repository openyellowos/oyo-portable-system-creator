# oyo-portable-system-creator 実装タスク分解書 v1

## 0. 文書の位置づけ

本書は、統合仕様書を**そのまま実装タスクへ落とし込める形**に再編成したものである。  
目的は、開発者が「何を・どの順番で・どのファイルに・どの受け入れ条件で」実装するかを明確にすることである。

- 対象バージョン: v1
- 対象OS: **open.Yellow.os のみ**
- 実装方式: **Python 正本**
- GUI: **PyQt6**
- 配布形式: **Debian パッケージ (.deb)**

---

## 1. v1 スコープ固定

### 1.1 実装対象

v1 で実装する機能は以下に限定する。

1. **現在のシステムを USB にコピーして portable system を作成する**
2. **既存の portable USB を別の USB にバックアップする**
3. **BIOS / UEFI 両対応の起動構成を作る**
4. **USB 向け最適化を firstboot で適用する**
5. **GUI と CLI の両方から同じコア処理を呼び出す**

### 1.2 v1 非対応

以下は v1 では実装しない。

- open.Yellow.os 以外の一般 Debian / Ubuntu 対応
- 復元機能
- 修復機能
- LVM / mdraid / dm-crypt コピー元対応
- ext4 以外の root filesystem 対応
- 差分バックアップ
- ネットワーク経由バックアップ

### 1.3 完了判定の前提

v1 完了とみなす条件は以下。

- GUI の主要導線で portable system 作成が完走できる
- CLI でも同等の処理が完走できる
- BIOS / UEFI で起動できる USB が作成できる
- 異常系で仕様どおりのエラーコードが返る
- firstboot により USB 向け最適化が適用される
- .deb としてインストールできる

---

## 2. 実装方針（正本）

### 2.1 方針

v1 は **shell スクリプト中心ではなく Python 中心**で実装する。  
外部コマンドは `subprocess` で呼ぶが、判断・エラー変換・ログ出力・後始末は Python 側で一元化する。

### 2.2 守るべき設計ルール

1. GUI と CLI は **同じ Workflow / Service を呼ぶ**
2. `print()` ベースの散在ログは禁止し、共通 logger を使う
3. 外部コマンド実行は必ず `CommandRunner` を通す
4. mount / bind / chroot / cleanup は共通ユーティリティに寄せる
5. 途中失敗時でも cleanup を必ず実行する
6. 破壊的操作前に安全チェックを完了していること

---

## 3. プロジェクト構成（実装単位）

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

## 4. 実装マイルストーン

### M1. プロジェクト土台作成

目的:
- 起動可能な Python アプリ骨格を作る
- GUI / CLI / core / services / infra の責務を分ける

成果物:
- ディレクトリ作成
- エントリポイント作成
- state / logger / command runner 作成

完了条件:
- GUI が起動する
- CLI ヘルプが表示される
- 共通 logger でログ出力できる

### M2. 事前検証・安全チェック

目的:
- 実行前に対象環境とデバイス安全性を判定する

成果物:
- OS 判定
- root 権限チェック
- 必須コマンドチェック
- 対象デバイス列挙
- root disk 除外
- 容量見積り

完了条件:
- 非対応環境で停止できる
- システムディスクを選べない
- 容量不足を検出できる

### M3. パーティション作成・ファイルシステム作成

目的:
- コピー先 USB を v1 仕様どおりに初期化する

成果物:
- GPT 作成
- EFI / root パーティション作成
- mkfs 実行
- ラベル設定
- マウント処理

完了条件:
- 仕様どおりの 2 パーティション構成が作成される
- 再実行時も既存マウント解除後に安全に作業できる

### M4. システムコピー

目的:
- 現在システムまたは portable USB を新しい USB へコピーする

成果物:
- rsync ベースコピー
- 除外パス適用
- `/etc/fstab` 生成
- machine-id / host key 再生成準備

完了条件:
- root filesystem がコピーされる
- 除外ルールが反映される
- fstab が新UUIDベースで生成される

### M5. ブート構成

目的:
- BIOS / UEFI 両対応の起動構成を完成させる

成果物:
- chroot 実行
- grub-install (BIOS)
- grub-install (UEFI)
- update-grub
- update-initramfs -u

完了条件:
- BIOS / UEFI の両方で起動に必要なファイルが配置される
- grub / initramfs エラー時に致命停止する

### M6. firstboot と USB 最適化

目的:
- 起動後に USB 向け最適化を一度だけ適用する

成果物:
- firstboot.service
- firstboot スクリプト
- tmpfs / journald / cache 設定
- 完了フラグ作成

完了条件:
- 初回起動時のみ処理される
- 2回目以降は再実行されない
- `/var/lib/oyo-portable/firstboot.done` が作成される

### M7. GUI 実装

目的:
- 仕様どおりのウィザード UI を実装する

成果物:
- モード選択
- コピー元確認
- コピー先選択
- オプション設定
- 最終確認
- 実行中画面
- 完了画面

完了条件:
- GUI から M2〜M6 を通せる
- 実行ログと進捗が表示される
- 致命エラー時にユーザー向けメッセージが表示される

### M8. CLI 実装

目的:
- GUI なしでも同じ処理を実行可能にする

成果物:
- `create` / `backup` サブコマンド
- dry-run
- yes/force
- verbose

完了条件:
- CLI で portable system 作成が完走できる
- GUI と同じエラーコード体系を使う

### M9. Debian パッケージ化

目的:
- .deb としてインストールできるようにする

成果物:
- `debian/` 一式
- `.desktop`
- polkit 定義
- 実行ラッパー

完了条件:
- `dpkg-buildpackage` でビルドできる
- インストール後に GUI / CLI が起動できる

### M10. テストとリリース判定

目的:
- v1 の受け入れ試験を完了する

成果物:
- 正常系テスト
- 異常系テスト
- 手順書
- 既知制約一覧

完了条件:
- 必須テストが全件 PASS
- 未実装事項が README / 仕様に明記される

---

## 5. タスク一覧（実装順）

以下は実装時の**着手順の正本**とする。

### T01. リポジトリ初期構成を作成する

対象:
- `src/`
- `debian/`
- `desktop/`
- `packaging/`

作業:
- ディレクトリを作成する
- `__init__.py` を必要箇所へ追加する
- 実行ファイルと import 経路を整える

受け入れ条件:
- `python3 -m src.main` 相当で起動できる
- import error が出ない

### T02. 共通 State モデルを作成する

対象:
- `src/core/state.py`

作業:
- 実行状態オブジェクトを定義する
- mode, source, target, options, progress, error を保持できるようにする

受け入れ条件:
- GUI / CLI / Workflow で同じ state を受け回せる

### T03. Logger を実装する

対象:
- `src/infra/logger.py`

作業:
- 画面表示用ログと詳細ログを分離する
- ログレベルを定義する
- 実行IDを採番できるようにする

受け入れ条件:
- 標準ログファイルへ追記できる
- GUI に進捗メッセージを流せる

### T04. CommandRunner を実装する

対象:
- `src/infra/command_runner.py`

作業:
- `subprocess.run()` の共通ラッパーを作る
- 標準出力 / 標準エラー / 終了コードを回収する
- 失敗時例外を共通化する
- 機密値マスク機能を入れる

受け入れ条件:
- すべての外部コマンド呼び出しが本クラス経由になる

### T05. OS / 権限 / 必須コマンドチェックを実装する

対象:
- `src/services/device_service.py`
- `src/core/workflow.py`

作業:
- `/etc/os-release` 判定
- root 権限判定
- 必須コマンド存在判定

受け入れ条件:
- 非対応OSで `E120`
- root なしで `E121`
- 必須コマンド不足で対応する致命エラー

### T06. デバイス列挙と安全判定を実装する

対象:
- `src/services/device_service.py`

作業:
- `lsblk --json` 取得
- リムーバブル判定
- root disk 判定
- 候補ディスク抽出
- 内蔵ディスク表示切替の基礎を作る

受け入れ条件:
- root disk が候補に出ない
- USB ディスクが一覧表示される

### T07. 容量見積りを実装する

対象:
- `src/services/device_service.py`

作業:
- `used_bytes("/") * 1.15 + 4GiB` を実装する
- 見積り根拠をログに残す

受け入れ条件:
- 容量不足時 `E202`
- GUI / CLI の両方で必要容量を表示できる

### T08. パーティション作成処理を実装する

対象:
- `src/services/partition_service.py`

作業:
- 対象デバイスのアンマウント
- GPT 作成
- EFI パーティション作成
- root パーティション作成
- パーティション再読込待ち

受け入れ条件:
- 既存マウントがあっても安全に解除できる
- 2 パーティション構成が作成される

### T09. mkfs / label / mount を実装する

対象:
- `src/services/partition_service.py`

作業:
- EFI を FAT32 で作成
- root を ext4 で作成
- ラベル設定
- 作業マウントポイントへ mount

受け入れ条件:
- EFI と root が所定位置へ mount される

### T10. コピー元判定を実装する

対象:
- `src/services/copy_service.py`

作業:
- mode=create では現在の `/` をコピー元とする
- mode=backup では portable USB の root mount をコピー元とする
- 非対応構成を弾く

受け入れ条件:
- create / backup でコピー元解決が分岐する
- ext4 以外で停止する

### T11. rsync コピーを実装する

対象:
- `src/services/copy_service.py`

作業:
- 常にコピーする対象を決める
- 除外パスを実装する
- rsync コマンドを組み立てる
- 失敗時 `E401`

受け入れ条件:
- コピー完了後に target root に主要ディレクトリが存在する
- 除外対象が混入しない

### T12. `/etc/fstab` 生成を実装する

対象:
- `src/services/copy_service.py`
- `src/templates/fstab.portable`

作業:
- 新規 UUID を取得する
- EFI / root 用エントリを生成する
- USB 向け mount option を反映する

受け入れ条件:
- target 側 `/etc/fstab` が新ディスク向けに書き換わる

### T13. chroot ユーティリティを実装する

対象:
- `src/infra/chroot.py`

作業:
- `/dev`, `/proc`, `/sys`, `/run` の bind mount
- chroot 実行
- cleanup 順序固定

受け入れ条件:
- 途中失敗でも cleanup される
- bind mount の取り残しがない

### T14. GRUB インストールを実装する

対象:
- `src/services/boot_service.py`

作業:
- BIOS 向け grub-install
- UEFI 向け grub-install
- `update-grub`
- 失敗時 `E501`

受け入れ条件:
- BIOS / UEFI の両方に必要ファイルが入る
- `update-grub` 失敗時に続行しない

### T15. initramfs 更新を実装する

対象:
- `src/services/boot_service.py`

作業:
- `update-initramfs -u`
- 失敗時は致命停止

受け入れ条件:
- 対象 root 内で initramfs が更新される

### T16. firstboot 生成を実装する

対象:
- `src/services/firstboot_service.py`
- `src/templates/firstboot.service`

作業:
- スクリプト配置
- service 配置
- `systemctl enable` 実行準備
- 完了フラグパスを統一

受け入れ条件:
- `/var/lib/oyo-portable/firstboot.done` を正として実装される

### T17. USB 最適化処理を実装する

対象:
- `src/services/optimize_service.py`
- `src/templates/tmpfs.conf`

作業:
- `/tmp`, `/var/tmp` tmpfs
- journald volatile
- `~/.cache` RAM化
- ブラウザキャッシュ RAM化
- APT cache RAM化
- machine-id 再生成準備
- SSH host key 再生成準備

受け入れ条件:
- firstboot 実行後に設定が反映される
- 再起動後も破綻しない

### T18. Workflow 本体を実装する

対象:
- `src/core/workflow.py`

作業:
- create / backup フローを実装する
- 進捗イベントを発行する
- cleanup を finally で保証する

受け入れ条件:
- GUI / CLI から同じ workflow を呼べる
- 中断時も後始末される

### T19. Controller を実装する

対象:
- `src/core/controller.py`

作業:
- GUI からの入力を state に反映する
- workflow 呼び出しを仲介する
- 画面遷移制御を持つ

受け入れ条件:
- GUI が controller 経由で動く

### T20. GUI ウィザードを実装する

対象:
- `src/gui/main_window.py`
- `src/gui/wizard_pages.py`

作業:
- 画面1: モード選択
- 画面2: コピー元確認 / バックアップ元選択
- 画面3: コピー先選択
- 画面4: オプション
- 画面5: 確認
- 画面6: 実行中
- 画面7: 完了

受け入れ条件:
- 一連の画面遷移が成立する
- 実行中画面で進捗とログが見える

### T21. CLI を実装する

対象:
- `src/bin/oyo-portable-system-cli`

作業:
- `create`
- `backup`
- `--yes`
- `--force`
- `--dry-run`
- `--verbose`

受け入れ条件:
- GUI なしで create / backup が実行できる

### T22. エラーコード体系を実装する

対象:
- `src/core/`
- `src/infra/`

作業:
- アプリ内例外とエラーコードの対応を定義する
- CLI 終了コードへ変換する
- GUI 向け文言を定義する

受け入れ条件:
- 代表エラーが仕様どおり返る

### T23. Debian パッケージングを実装する

対象:
- `debian/*`
- `desktop/*`
- `packaging/*`

作業:
- control / rules / install を整備する
- .desktop を配置する
- polkit ラッパーを配置する

受け入れ条件:
- .deb をビルドしてインストールできる
- メニューから GUI を起動できる

### T24. README / 運用ドキュメントを整備する

対象:
- `README.md`

作業:
- 使い方
- 対応範囲
- 非対応範囲
- 既知制約
- テスト方法

受け入れ条件:
- 初見の開発者がビルドと実行方法を追える

---

## 6. ファイル別の責務

### `src/core/state.py`

責務:
- 実行状態の保持
- GUI / CLI 共通の入力値モデル
- workflow の中間結果保持

必須項目:
- mode
- source_device
- target_device
- used_bytes
- required_bytes
- options
- progress_percent
- current_step
- error_code
- error_message

### `src/core/workflow.py`

責務:
- 実行順序の正本
- 進捗通知
- cleanup 保証

必須メソッド:
- `run_create()`
- `run_backup()`
- `precheck()`
- `cleanup()`

### `src/services/device_service.py`

責務:
- OS チェック
- 権限チェック
- 必須コマンドチェック
- デバイス列挙
- 容量見積り

### `src/services/partition_service.py`

責務:
- unmount
- partition 作成
- mkfs
- mount
- UUID / label 取得

### `src/services/copy_service.py`

責務:
- コピー元解決
- rsync 実行
- 除外ルール
- fstab 書き換え

### `src/services/boot_service.py`

責務:
- chroot に入って bootloader を整備する
- grub-install
- update-grub
- update-initramfs

### `src/services/firstboot_service.py`

責務:
- 初回起動 service / script 配置
- enable 状態の準備

### `src/services/optimize_service.py`

責務:
- USB 向け最適化ファイル生成
- tmpfs / cache / journald 反映
- machine-id / SSH host key 再生成準備

### `src/infra/command_runner.py`

責務:
- 外部コマンドの唯一の実行口

### `src/infra/chroot.py`

責務:
- bind mount / unmount / chroot 実行の共通化

### `src/gui/wizard_pages.py`

責務:
- UI 部品と入力検証

### `src/gui/main_window.py`

責務:
- 画面全体の構成
- controller 接続

---

## 7. データ・仕様の統一ルール

### 7.1 パーティション構成

v1 の正本は以下。

1. **EFI System Partition**
   - FAT32
   - GPT
2. **root partition**
   - ext4
   - 通常の Linux root filesystem

※ v1 では persistence / overlay 用パーティションは作らない。

### 7.2 コピー対象ルール

常にコピーする主対象:
- `/bin`
- `/boot`
- `/etc`
- `/home`
- `/lib*`
- `/opt`
- `/root`
- `/sbin`
- `/srv`
- `/usr`
- `/var`

除外候補:
- `/dev/*`
- `/proc/*`
- `/sys/*`
- `/run/*`
- `/tmp/*`
- `/mnt/*`
- `/media/*`
- lost+found
- 必要に応じたキャッシュ一時領域

### 7.3 完了フラグ

firstboot 完了フラグは以下に固定する。

```text
/var/lib/oyo-portable/firstboot.done
```

### 7.4 ログ出力先

推奨正本:

- ユーザー向け実行ログ: `/var/log/oyo-portable-system-creator.log`
- 詳細ログ: `/var/log/oyo-portable-system-creator-debug.log`

firstboot 後に journald volatile を使う場合でも、実装時点では**ログパス名をこの2つで統一**する。

---

## 8. エラーコード実装表

| コード | 意味 | 停止 | 実装箇所 |
|---|---|---:|---|
| E120 | 非対応OS | 必須 | device_service |
| E121 | root権限不足 | 必須 | device_service |
| E122 | 必須コマンド不足 | 必須 | device_service |
| E201 | コピー先デバイス不正 | 必須 | device_service |
| E202 | 容量不足 | 必須 | device_service |
| E203 | root disk / 危険デバイス選択 | 必須 | device_service |
| E301 | パーティション作成失敗 | 必須 | partition_service |
| E302 | mkfs失敗 | 必須 | partition_service |
| E303 | mount失敗 | 必須 | partition_service |
| E401 | rsync失敗 | 必須 | copy_service |
| E402 | fstab生成失敗 | 必須 | copy_service |
| E501 | grub-install / update-grub失敗 | 必須 | boot_service |
| E502 | initramfs更新失敗 | 必須 | boot_service |
| E601 | firstboot準備失敗 | 必須 | firstboot_service |
| E699 | cleanup失敗 | 警告 | workflow/chroot |
| E999 | 想定外例外 | 必須 | controller/workflow |

---

## 9. 受け入れ試験項目

### 9.1 正常系

1. open.Yellow.os 上で GUI から create が完走する
2. open.Yellow.os 上で CLI から create が完走する
3. 既存 portable USB を backup できる
4. 作成した USB が BIOS で起動する
5. 作成した USB が UEFI で起動する
6. firstboot が一度だけ実行される

### 9.2 異常系

1. open.Yellow.os 以外で `E120`
2. root なしで `E121`
3. 必須コマンド不足で `E122`
4. root disk 選択で `E203`
5. 容量不足で `E202`
6. rsync 失敗で `E401`
7. grub 失敗で `E501`
8. firstboot 配置失敗で `E601`

### 9.3 cleanup 試験

1. mount 後失敗しても umount される
2. bind mount 後失敗しても解除される
3. 途中キャンセル後に再実行できる

---

## 10. 実装順序の推奨

着手順は以下を推奨する。

1. T01〜T04: 基盤
2. T05〜T07: 事前検証
3. T08〜T09: partition / mount
4. T10〜T12: copy
5. T13〜T15: boot
6. T16〜T17: firstboot / optimize
7. T18〜T21: workflow / GUI / CLI
8. T22〜T24: error / package / docs

この順番にすることで、まず **CLI 先行で end-to-end を成立**させ、その後 GUI を載せる流れにできる。

---

## 11. 着手判定（Definition of Ready）

以下を満たした時点で実装着手可とする。

- v1 スコープが open.Yellow.os 限定で固定されている
- 2 パーティション構成が正本として確定している
- エラーコード表が確定している
- firstboot 完了フラグのパスが確定している
- ログパスが確定している
- Python 正本構成が確定している

---

## 12. 実装完了判定（Definition of Done）

以下をすべて満たしたら v1 実装完了とする。

- 必須タスク T01〜T24 が完了している
- 正常系 / 異常系 / cleanup 試験が通っている
- GUI / CLI の両導線が成立している
- BIOS / UEFI 起動確認が取れている
- .deb ビルドとインストール確認が取れている
- README に既知制約が記載されている

---

## 13. 開発メモ

- まずは **CLI だけで create が最後まで動くこと**を最優先にする
- GUI は core が固まってから載せる
- shell スクリプト断片を増やさず、Python サービスへ寄せる
- 途中失敗よりも cleanup 漏れの方が危険なので、cleanup を最優先で固める
- v1 では「広く対応」より「open.Yellow.os で確実に動く」を優先する

