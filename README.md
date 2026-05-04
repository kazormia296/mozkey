Mozc fork by koyasi777
======================

This repository is my personal fork of [google/mozc](https://github.com/google/mozc).

このリポジトリは [google/mozc](https://github.com/google/mozc) の個人用フォークです。

Privacy / Network Access
------------------------

This fork is designed to run without network communication from Mozc runtime
processes during normal IME operation.

The usage-statistics and crash-report option inherited from upstream has been
removed from the administration and configuration dialogs.

The default `StatsConfigUtil` implementation is fixed to the null implementation
in this fork, and usage statistics cannot be enabled through the normal runtime
path.

Windows release binaries are checked so that Mozc runtime executables do not
import common networking libraries such as `ws2_32.dll`, `winhttp.dll`,
`wininet.dll`, or `urlmon.dll`.

Additional release checks verify that Mozc runtime binaries do not contain
hard-deny telemetry, updater, crash-upload, or usage-statistics markers.

Generic URL-like markers such as `http://`, `https://`, and `googleapis.com`
are reported for audit, but they are not treated as hard failures because they
can come from manifests, XML namespaces, comments, or library metadata.

Building from source may require network access to download build dependencies.
This is separate from runtime behavior of the installed IME.

On Windows, the installer also adds outbound Windows Firewall block rules for
Mozc runtime executables as an additional offline hardening layer. These rules
are removed during uninstall.

See also:

- [Secure Offline Guarantee](docs/security/offline_guarantee.md)
- [Secure Offline Release Checklist](docs/security/release_checklist.md)

プライバシー / ネットワークアクセス
------------------------------------

この fork は、通常の IME 利用時に Mozc の実行時プロセスがネットワーク通信を
行わない構成を目指しています。

upstream 由来の使用統計・クラッシュレポート送信オプションは、管理ダイアログ
および設定ダイアログから削除しています。

この fork では `StatsConfigUtil` のデフォルト実装を Null 実装に固定しており、
通常の実行経路から使用統計を有効化できません。

Windows 向けリリースバイナリについては、`ws2_32.dll`, `winhttp.dll`,
`wininet.dll`, `urlmon.dll` などの代表的なネットワーク関連 DLL を import
していないことを検査します。

さらに、リリース時にはテレメトリ、アップデータ、クラッシュアップロード、使用統計
関連の危険な marker が Mozc 実行時バイナリに含まれないことを確認します。

`http://`, `https://`, `googleapis.com` などの汎用的な URL 風 marker は、
manifest、XML namespace、コメント、ライブラリ metadata に由来する場合があるため、
監査用に表示しますが hard failure にはしていません。

ソースからビルドする場合、ビルド依存関係の取得にネットワーク接続が必要になる
場合があります。これは、インストール済み IME の実行時通信とは別です。

Windows 版では、追加のオフライン防御層として、インストーラーが Mozc の実行ファイルに
outbound 通信をブロックする Windows Firewall rule を追加します。これらの rule は
アンインストール時に削除されます。

関連ドキュメント:

- [Secure Offline Guarantee](docs/security/offline_guarantee.md)
- [Secure Offline Release Checklist](docs/security/release_checklist.md)

Download / Install
------------------

Windows 用のビルド済み MSI は [Releases](https://github.com/koyasi777/mozc/releases) からダウンロードできます。

- 通常の 64-bit Windows では `Mozc64_myproduct_v0.4.2_offline_x64.msi` を使用してください。
- 本 fork のリリースは個人用の experimental build として公開しています。

> [!WARNING]
> このビルドは google/mozc の公式配布物ではありません。
> 個人用 fork の experimental / pre-release build です。
> MSI は署名されていないため、Windows の警告が表示される場合があります。

Main branches
-------------

- `my-product`: my main branch for daily use
- `master`: upstream tracking branch
- `pr/*`: upstream-oriented proposal branches

主な追加機能（`my-product`）
---------------------------

- 曖昧なローマ字規則でも途中表示できるオプションを追加
- ローマ字テーブル編集画面に、そのオプション用のチェックボックス UI を追加
- 句読点・記号を単打で確定できるオプションを追加
- 単打確定の対象を設定画面のチェックボックスで選択可能
- 句読点変換と句読点・記号の単打確定は排他的に動作
- 変換確定直後に Backspace や Cancel キーで取り消した場合のユーザー履歴学習の扱いを改善
- ライブ変換機能を追加。未確定文字列を自動変換し、確定前の読みをルビ風 overlay で表示
- ライブ変換は設定画面から ON/OFF と変換開始までの遅延時間を変更可能
- ライブ変換は入力直後の不要な変換ちらつきを抑えるため、文字入力後に短いデバウンスを挟んで実行
- 1文字だけの未確定文字列では、助詞などの誤変換を避けるためライブ変換を実行しない
- Windows 版で左 Shift / 右 Shift / 左 Ctrl / 右 Ctrl を個別キーとして設定画面から割り当て可能
- Windows 版で IMEOn / IMEOff に割り当てたキーを押した場合、すでに同じ状態でも IME モードインジケータを表示
- Windows 版の候補ウィンドウにダークモード切り替えを追加
- Windows 版で未確定文字の文字色・背景色・下線色を設定画面からカスタマイズ可能
- Windows 版の候補ウィンドウや IME 切り替えインジケータの配色・余白・角丸などの見た目を調整
- 自分の Windows 開発環境向けのビルド調整

Examples
--------

### Ambiguous romaji display

With the custom option enabled:

- `ms -> ます`
- `mst -> ました`

Typing `ms` shows `ます` in preedit, and then typing `t` updates the preedit to `ました`.

このオプションを有効にすると、たとえば

- `ms -> ます`
- `mst -> ました`

のような規則で、`ms` を入力した時点で `ます` を未変換表示し、続けて `t` を入力すると `ました` に更新されます。

If the input temporarily follows a longer ambiguous rule but the longer rule
later fails, Mozc falls back to the last displayed ambiguous result and replays
the remaining input.

For example, with rules such as:

- `ctn -> ことに`
- `ctnnr -> ことになる`
- `ctnnc -> ことなのか`

typing `ctnnaru` is interpreted as `ctn + naru`, resulting in `ことになる`,
while valid longer rules such as `ctnnr` and `ctnnc` still work.

長い曖昧規則の途中まで進んだ後、その規則が成立しない場合は、最後に表示できていた曖昧結果まで戻り、残りの入力を通常のローマ字として再解釈します。

たとえば

- `ctn -> ことに`
- `ctnnr -> ことになる`
- `ctnnc -> ことなのか`

のような規則がある場合、`ctnnaru` は `ctn + naru` として解釈され、`ことになる` になります。
一方で、`ctnnr` や `ctnnc` のように長い規則として成立する入力は、従来どおりその規則が使われます。

### Live conversion

With live conversion enabled, Mozc automatically converts the current composition without committing it immediately.

To reduce distracting intermediate conversions, this fork applies live conversion after a short configurable debounce delay instead of converting every character immediately. Single-character compositions are not live-converted, because they are often particles such as `に`, `を`, or `が`.

For example:

- Type `kyouha`
- After the debounce delay, the preedit can be shown as `今日は` before pressing Space
- Typing more characters keeps the same uncommitted composition and schedules another live conversion
- Pressing Backspace or Delete updates the live conversion result immediately
- Pressing Enter commits the current live conversion result

During live conversion, this fork shows a small ruby-like overlay window above the preedit text so that the original reading remains visible while the converted text is shown.

The live conversion feature can be enabled or disabled from the config dialog. The debounce delay can also be configured there.

### ライブ変換

ライブ変換を有効にすると、スペースキーを押さなくても、入力中の未確定文字列が自動で変換されます。

入力途中の不要な中間変換表示を抑えるため、この fork では文字入力後に短い設定可能なデバウンス時間を挟んでからライブ変換を実行します。`に`、`を`、`が` のような助詞として使われやすい入力を誤って漢字化しないように、1文字だけの未確定文字列ではライブ変換を行いません。

たとえば:

- `kyouha` と入力
- デバウンス時間の経過後、Space を押す前に未確定文字列が `今日は` のように表示される
- 続けて文字を入力しても途中の変換結果は確定されず、同じ未確定文字列として再変換される
- Backspace / Delete では、削除後の状態をすぐにライブ変換結果へ反映する
- Enter で現在のライブ変換結果を確定する

ライブ変換中は、変換後の文字を表示しながら元の読みも分かるように、未確定文字の上付近に Mozc 独自のルビ風 overlay window を表示します。

ライブ変換は設定画面から ON/OFF を切り替えられます。また、変換開始までの遅延時間も設定画面から変更できます。

### Direct commit for punctuations/symbols

With the direct-commit option enabled, configured punctuations/symbols are committed immediately.

Examples:

- `tesuto.` → `てすと。`
- `tesuto?` → `てすと？`
- `kakko(` → `かっこ（`

You can choose which punctuations/symbols are committed directly in the config dialog.

句読点・記号の単打確定オプションを有効にすると、設定した記号を入力した時点で未確定文字列全体をそのまま確定できます。

たとえば

- `tesuto.` → `てすと。`
- `tesuto?` → `てすと？`
- `kakko(` → `かっこ（`

のように動作します。

どの句読点・記号を単打確定の対象にするかは、設定画面のチェックボックスで選択できます。

When live conversion is enabled, direct-commit punctuations/symbols commit the currently displayed live conversion result instead of committing the raw kana composition.

ライブ変換が有効な場合、句読点・記号の単打確定では、ひらがなの未変換文字列ではなく、現在表示されているライブ変換結果を確定します。

### Partial revert of history learning after immediate correction

When a committed conversion is immediately followed by Backspace, or by a key assigned to Cancel in the current keymap, this fork treats it as a revert signal for the just-committed learning result.

If the entire committed text is erased, the just-committed suggestion history and user segment history do not continue to affect later suggestions or conversions.

If only the tail of the committed text is erased with Backspace, the history for the remaining part is kept while the erased tail is reverted.

For example:

- Commit `いしとは` as `医師とは`
- Press Backspace twice and erase only `とは`
- The remaining `医師` history is kept
- The tail-specific learning for `医師とは` is reverted

This makes immediate correction after conversion confirmation behave closer to user intent.

### 確定直後の修正による履歴学習の取り消し

変換確定直後に Backspace、または現在のキー設定で Cancel に割り当てられたキーが入力された場合、このフォークでは直前に確定した学習結果を取り消し対象として扱います。

確定した文字列全体を削除した場合は、その確定によるサジェスト履歴およびユーザーセグメント履歴が、以後のサジェストや変換順位に残らないようにします。

一方で、Backspace により確定文字列の末尾だけを削除した場合は、残っている部分の履歴は保持し、削除された末尾に対応する履歴だけを取り消します。

たとえば:

- `いしとは` を `医師とは` として確定
- Backspace を2回押して `とは` だけを削除
- 残っている `医師` の履歴は保持
- `医師とは` としての末尾込みの学習は取り消し

これにより、変換確定直後の修正操作がユーザーの意図に近い形で履歴学習へ反映されます。

### Independent left/right Shift and Ctrl key bindings (Windows)

On Windows, left/right Shift and left/right Ctrl can be configured as separate keys in the keybinding editor.

For example:

- `DirectInput + RightShift -> IMEOn`
- `Precomposition + LeftShift -> IMEOff`
- `Composition + LeftShift -> IMEOff`
- `Conversion + LeftShift -> IMEOff`
- `DirectInput + RightCtrl -> IMEOn`
- `Precomposition + LeftCtrl -> IMEOff`

This allows assigning different IME actions to the left and right Shift/Ctrl keys.

Generic Ctrl key bindings such as `Ctrl j` continue to work with either left or right Ctrl.

When a key is explicitly assigned to `IMEOn` or `IMEOff`, the mode indicator is
also shown even if Mozc is already in the requested state.  For example, pressing
an `IMEOff` key while IME is already off still shows the direct-input indicator.
This makes mode-confirmation keys useful as explicit visual feedback, not only
as state-changing toggles.

Windows 版では、キー設定エディタ上で左 Shift / 右 Shift / 左 Ctrl / 右 Ctrl を別々のキーとして扱えます。

`Ctrl j` のような従来の汎用 Ctrl キーバインドは、左 Ctrl / 右 Ctrl のどちらでも従来どおり動作します。

たとえば

- `DirectInput + RightShift -> IMEOn`
- `Precomposition + LeftShift -> IMEOff`
- `Composition + LeftShift -> IMEOff`
- `Conversion + LeftShift -> IMEOff`
- `DirectInput + RightCtrl -> IMEOn`
- `Precomposition + LeftCtrl -> IMEOff`

のように設定でき、左右の Shift / Ctrl に別々の IME 操作を割り当てられます。

`IMEOn` または `IMEOff` に明示的に割り当てたキーを押した場合は、すでにその状態であっても
IME モードインジケータを表示します。たとえば、IME がすでに無効の状態で `IMEOff` キーを
押しても、直接入力状態のインジケータを表示します。

これにより、IME 有効化・無効化キーを「状態を切り替えるキー」としてだけでなく、
現在状態を視覚的に確認するためのキーとしても使えます。

### Windows candidate window theme

On Windows, the candidate window can be switched between the default light theme and a dark theme from the config dialog.

The dark theme also adjusts the candidate window appearance, including colors, spacing, rounded corners, and footer visibility, to make it look more modern.

Windows 版では、設定画面から候補ウィンドウの通常テーマとダークテーマを切り替えられます。

ダークテーマでは配色だけでなく、余白、角丸、フッター表示なども調整し、候補ウィンドウ全体の見た目をよりモダンにしています。

### Windows preedit display colors

On Windows, preedit display colors can be customized from the config dialog.

The following display attributes can be configured separately for input text and the converting segment:

- Text color
- Background color
- Underline color

This is intended to improve the visibility of uncommitted text, especially for users who need stronger visual contrast while composing or converting Japanese text.

The setting is applied through Windows TSF display attributes. It works only in applications that honor those attributes. Some applications, including Chrome and Edge, may ignore them.

Windows 版では、設定画面から未確定文字の表示色をカスタマイズできます。

入力中の文字と変換中の文節について、それぞれ以下を個別に設定できます。

- 文字色
- 背景色
- 下線色

日本語入力中・変換中の未確定文字を見やすくするための機能です。特に、視認性を高めたいユーザー向けのアクセシビリティ改善として追加しています。

この設定は Windows TSF の表示属性として反映されます。対応アプリでのみ有効です。Chrome や Edge など、一部のアプリでは反映されない場合があります。

Note
----

This fork is primarily maintained for personal use.
Upstream-oriented changes are organized in `pr/*` branches.

以下は upstream の README です。

[Mozc - a Japanese Input Method Editor designed for multi-platform](https://github.com/google/mozc)
===================================

Copyright 2010-2026 Google LLC

Mozc is a Japanese Input Method Editor (IME) designed for multi-platform such as
Android OS, Apple macOS, Chromium OS, GNU/Linux and Microsoft Windows.  This
OpenSource project originates from
[Google Japanese Input](http://www.google.com/intl/ja/ime/).

Mozc is not an officially supported Google product.


What's Mozc?
------------
For historical reasons, the project name *Mozc* has two different meanings:

1. Internal code name of Google Japanese Input that is still commonly used
   inside Google.
2. Project name to release a subset of Google Japanese Input in the form of
   source code under OSS license without any warranty nor user support.

In this repository, *Mozc* means the second definition unless otherwise noted.

Detailed differences between Google Japanese Input and Mozc are described in [About Branding](docs/about_branding.md).

Build Instructions
------------------

* [How to build Mozc for Android](docs/build_mozc_for_android.md): for Android library (`libmozc.so`)
* [How to build Mozc for Linux](docs/build_mozc_for_linux.md): for Linux desktop
* [How to build Mozc for macOS](docs/build_mozc_in_osx.md): for macOS build
* [How to build Mozc for Windows](docs/build_mozc_in_windows.md): for Windows

Release Plan
------------

tl;dr. **There is no stable version.**

As described in [About Branding](docs/about_branding.md) page, Google does
not promise any official QA for OSS Mozc project.  Because of this,
Mozc does not have a concept of *Stable Release*.  Instead we change version
number every time when we introduce non-trivial change.  If you are
interested in packaging Mozc source code, or developing your own products
based on Mozc, feel free to pick up any version.  They should be equally
stable (or equally unstable) in terms of no official QA process.

[Release History](docs/release_history.md) page may have additional
information and useful links about recent changes.

License
-------

All Mozc code written by Google is released under
[The BSD 3-Clause License](http://opensource.org/licenses/BSD-3-Clause).
For third party code under [src/third_party](src/third_party) directory,
see each sub directory to find the copyright notice.  Note also that
outside [src/third_party](src/third_party) following directories contain
third party code.

### [src/data/dictionary_oss/](src/data/dictionary_oss)
Mixed.
See [src/data/dictionary_oss/README.txt](src/data/dictionary_oss/README.txt)

### [src/data/test/dictionary/](src/data/test/dictionary)
The same as [src/data/dictionary_oss/](src/data/dictionary_oss).
See [src/data/dictionary_oss/README.txt](src/data/dictionary_oss/README.txt)

### [src/data/test/stress_test/](src/data/test/stress_test)
Public Domain.  See the comment in
[src/data/test/stress_test/sentences.txt](src/data/test/stress_test/sentences.txt)
