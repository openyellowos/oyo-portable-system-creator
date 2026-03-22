## 1. このアプリは何か

`oyo-portable-system-creator` は、**現在の open.Yellow.os 環境を USB に複製し、持ち運び可能な Portable USB を作成するための専用ツール**です。

一般的な USB イメージ書き込みツールではなく、**今使っている open.Yellow.os の状態をベースに、起動可能な portable system を新しい USB へ構築する**ことを目的にしています。

---

## 2. できること

v1 時点の主な機能は以下です。

### 2.1 Portable USB の新規作成

現在の open.Yellow.os システムをコピー元として、コピー先 USB に portable system を作成します。

- コピー先 USB の安全チェック
- GPT での再初期化
- EFI パーティションと ext4 root パーティションの作成
- システムファイルの rsync コピー
- `fstab` の再生成
- GRUB / initramfs の設定
- USB 向け最適化設定の適用

---

## 3. このアプリの特徴

### 3.1 起動構成まで自動化

単にファイルをコピーするだけでなく、**起動可能な USB として成立するところまで自動で構成**します。

- BIOS 起動向け GRUB 配置
- UEFI 起動向け EFI バイナリ配置
- portable 用 `grub.cfg` 生成
- `initramfs` 更新

### 3.2 USB 向けの負荷軽減を考慮

USB 媒体で使うことを前提に、ログやキャッシュの扱いを調整します。

- journald を揮発化
- 一部ログ・キャッシュを RAM 側へ逃がす
- apt キャッシュを `/tmp` 側へ寄せる
- ブラウザの一時キャッシュ類をセッション RAM 側へ逃がす

### 3.3 初回起動時の再生成処理

複製したままでは衝突しやすい情報を、初回起動時に再生成します。

- `machine-id`
- SSH ホスト鍵

---

## 4. 処理の流れ

Portable USB 作成時の大まかな流れは次の通りです。

1. 実行環境チェック
2. root 権限・必須コマンド確認
3. コピー先 USB の妥当性確認
4. 必要容量の見積もり
5. コピー先 USB のパーティション再作成
6. EFI / root ファイルシステム作成とマウント
7. システムの rsync コピーを 2 回実行
8. `fstab` を新しい UUID で生成
9. GRUB と initramfs を更新
10. USB 向け最適化を適用
11. firstboot サービスを組み込み
12. 後始末としてアンマウント

ポイントは、**「単純コピー」ではなく「portable として起動・運用できる状態まで仕上げる」**ことです。

---

## 5. 安全性の考え方

このアプリは破壊的操作を含むため、事前チェックを重視しています。

- システムディスクはコピー先に選べない
- USB / リムーバブルデバイスのみを対象にする
- 容量不足を事前検知する
- 必須コマンド不足を事前検知する
- 失敗時でも cleanup でアンマウントを試みる

---

## 6. 補足

開発上の構成としては、GUI / CLI の上に共通 Workflow があり、その下で以下のサービスが役割分担しています。

- `DeviceService`: 環境確認、デバイス列挙、安全チェック
- `PartitionService`: パーティション作成、ファイルシステム作成、マウント
- `CopyService`: rsync コピー、除外ルール、`fstab` 生成
- `BootService`: GRUB / initramfs / EFI 起動構成
- `OptimizeService`: USB 向け最適化
- `FirstbootService`: 初回起動処理組み込み
