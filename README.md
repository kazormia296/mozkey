Mozc fork by koyasi777
======================

This repository is my personal fork of [google/mozc](https://github.com/google/mozc).

このリポジトリは [google/mozc](https://github.com/google/mozc) の個人用フォークです。

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
- Windows 版で左 Shift / 右 Shift を個別キーとして設定画面から割り当て可能
- Windows 版の候補ウィンドウにダークモード切り替えを追加
- Windows 版の候補ウィンドウの配色・余白・角丸などの見た目を調整
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

### Independent left/right Shift key bindings (Windows)

On Windows, left Shift and right Shift can be configured as separate keys in the keybinding editor.

For example:

- `DirectInput + RightShift -> IMEOn`
- `Precomposition + LeftShift -> IMEOff`
- `Composition + LeftShift -> IMEOff`
- `Conversion + LeftShift -> IMEOff`

This allows assigning different IME actions to the left and right Shift keys.

Windows 版では、キー設定エディタ上で左 Shift と右 Shift を別々のキーとして扱えます。

たとえば

- `DirectInput + RightShift -> IMEOn`
- `Precomposition + LeftShift -> IMEOff`
- `Composition + LeftShift -> IMEOff`
- `Conversion + LeftShift -> IMEOff`

のように設定でき、左右の Shift に別々の IME 操作を割り当てられます。

### Windows candidate window theme

On Windows, the candidate window can be switched between the default light theme and a dark theme from the config dialog.

The dark theme also adjusts the candidate window appearance, including colors, spacing, rounded corners, and footer visibility, to make it look more modern.

Windows 版では、設定画面から候補ウィンドウの通常テーマとダークテーマを切り替えられます。

ダークテーマでは配色だけでなく、余白、角丸、フッター表示なども調整し、候補ウィンドウ全体の見た目をよりモダンにしています。

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
