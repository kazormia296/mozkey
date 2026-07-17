# Grimodex Linux native integration

Status: Accepted, implementation in progress (2026-07-17)

## Decision

Linux の Grimodex IME は、Hazkey を変換・session owner として拡張する構成から、
最新 Mozkey を fork し Mozc session を唯一の状態所有者とする構成へ移行する。

採用する構成は次の通り。

```text
Grimodex
  -> Snapshot Protocol v1 JSON (atomic publish)
  -> mozc_server
       - strict snapshot loader
       - immutable project dictionary registry
       - composition-pinned native dictionary overlay
       - Mozkey conversion, history and Zenz
  <- existing Mozc protobuf IPC
  <- Fcitx5 Mozkey adapter (one Mozc session per InputContext)
```

Hazkey の Mozc sidecar は移行判断の比較 oracle として研究 branch/tag および評価
artifact にのみ保持し、新しい product path や installable fallback にはしない。
Hazkey は未リリースのため、Mozkey release gate に Hazkey runtime への rollback や
installed round trip を含めない。

## Why Mozkey

今回の spike では、Hazkey を継続採用するだけの再現可能な品質・速度上の優位を
確認できなかった。一方 Mozkey には live conversion、学習取り消し、候補表示、
daily dictionary、Zenz feedback など session 層と密接な独自改善が継続的に入って
いる。これらを sidecar 越しに再実装するより、現在の Mozkey を基点に Linux adapter
と Grimodex の固有契約を移植する方が、長期の upstream 追随と機能整合性に優れる。

これは `fcitx-mozkey` の古い fork をそのまま採用する決定ではない。基点は Mozkey の
最新 release/main とし、Fcitx 公式 tree と必要な Linux Zenz 差分を履歴の分かる単位で
載せ直す。

## State ownership

変換中の preedit、文節、候補、candidate ID、live conversion、履歴、undo、Zenz の
状態は Mozc session だけが所有する。Fcitx adapter は key/focus/security context の
入力と Mozc output の描画だけを担当し、独立 reducer や候補状態を持たない。

各 Fcitx `InputContext` は独立した Mozc session を持つ。同じ program の複数 window
も session を共有しない。program、frontend、secure domain、focus epoch が変わった
場合、旧 domain の candidate、effect、commit を新 session へ replay しない。

## Cross-platform boundary

共有するのは platform 固有の wire や converter implementation ではなく、次の
semantics と fixture である。

- Grimodex Snapshot Protocol v1 の JSON schema、validation、size limits
- project scope と composition generation pin
- secure input、learning、Zenz、surrounding context の不変条件
- composition behavior scenario と期待結果

Linux 内部は既存 Mozc protobuf IPC、macOS と Windows は各 OS の adapter を使って
よい。辞書 entry 自体を Mozc IPC に載せず、`mozc_server` が同一 user の immutable
JSON snapshot を直接読む。

## Project dictionary

project dictionary は Mozc の永続 user dictionary に import しない。immutable な
in-memory `ProjectDictionarySnapshot` として通常 dictionary より前に参照し、token、
node、candidate に専用 provenance を伝播する。project candidate は少なくとも
`NO_LEARNING`、`NO_DELETABLE`、`NO_VARIANTS_EXPANSION` を持ち、personal history と
project data を混ぜない。

新しい composition の開始時だけ最新 snapshot を pin する。composition 中に A から
B へ publish されても、その composition は A を使い続ける。submit、revert、reset
または session 終了後、次の composition から B を使う。識別には単調 sequence だけで
なく payload SHA-256 を使う。

## Snapshot loading

publisher は project temp の write/fsync/rename、projects directory fsync、state temp の
write/fsync/rename、root directory fsync の順で publish する。loader は state S1、
project、state S2 の順で読み、S1 と S2 の exact bytes または digest が一致した時だけ
公開する。

Linux loader は次を fail-closed で検証する。

- root と projects は実効 UID 所有、`0700`、symlink ではない
- state と project は実効 UID 所有の regular file
- file は symlink、FIFO、device ではなく、group/other permission を持たない
- `openat2` の `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS`、または directory fd と
  `openat(O_NOFOLLOW | O_CLOEXEC)` を使う
- state は 64 KiB、project は 16 MiB、entry は 20,000 件を上限とする
- project ID、entry ID、reading、surface、timestamp、condition の既存上限を守る

inotify は更新通知の hint としてのみ使う。composition boundary でも最新 state を
再確認し、parse/index 構築後に完成済み `shared_ptr<const Snapshot>` を atomic publish
する。

Fcitx addon は起動時と15分ごとに
`consumers/fcitx5-mozkey.json` を private/atomic に更新する。これが Grimodex の
`auto` 連携を有効にする唯一の runtime identity であり、Mozc/Hazkey の consumer
fileを借用しない。通常のFcitx再起動では短い検出切れを避けるためfileを残し、
uninstall時だけ明示helperでこの1 fileを削除する。state、project snapshot、他IMEの
consumer fileは削除しない。heartbeatは毎回 `/usr/lib/mozkey/mozc_server` を前後2回
確認し、package removal後も古いaddonがFcitxにmapされたままならconsumerを削除する。
uninstallerはこのruntime markerを先に外し、続けてnative unregister helperを実行する。
Zenz capabilityはserver markerだけでは宣言せず、scorer、immutable GGUF、実行可能な
llama runtimeが揃う時だけtrueとする。heartbeatはfeature/install completeness、
Zenz runtime probeはmodel loadとauthenticated localhost `/completion` readinessを表し、
両者を同一の判定として扱わない。

## Secure input

secure は常に stale generation より優先する。Fcitx の `PasswordOrSensitive` を Mozc
context に明示し、secure 中は surrounding text を送らない。server は session の pin、
候補、context、undo、Zenz state、learning pending、replay history を同期的に破棄する。

application scope の既定は fail-closed の `grimodex-only` とする。Fcitx が渡す
`program` を ASCII trim/lowercase した値が exact `grimodex` または
`com.miyakey.grimodex` の時だけ project dictionary と Zenz project context を pinする。
空値、`electron`、path、substring、helper名は許可しない。明示的なローカル設定
`MOZKEY_GRIMODEX_SCOPE=all` だけが全アプリへ広げ、`off` および未知の非空設定値は
無効化する。通常の Mozkey 変換・学習は scope 外アプリでも継続する。

secure purge は対象 session の参照を切る操作であり、別の非 secure session が pin
している global immutable snapshot を消してはならない。secure 解除後に古い snapshot
を暗黙復活させず、新しく検証した publish を次の composition で pin する。

## Recovery

Mozc server/session restart 後、旧 candidate ID と commit/effect は全て失効する。
non-secure composition は、元 snapshot digest がまだ利用可能な場合に限って復旧できる。
digest がなければ旧操作を replay せず、Fcitx が保持した raw reading を最新 snapshot の
新しい composition として再入力する。secure composition は raw reading も復旧しない。

通常の Mozc IPC command/response には allocation 前の hard cap を設ける。辞書 payload
を IPC に載せないため、この上限を小さく維持できる。

## Release gates

既定 IME の切替には、少なくとも次を全て満たす必要がある。

1. 最新 Mozkey の Linux `mozc_server` と Fcitx addon が reproducible に build できる。
2. Mozkey live conversion と Linux Zenz の対象 test が通る。
3. Protocol v1 の valid/invalid/malicious fixture が Hazkey と同じ結果になる。
4. project dictionary が composition 単位で pin され、project candidate が学習されない。
5. secure no-context/no-Zenz/no-learning/purge が unit と real process で通る。
6. 同一 program を含む multi-InputContext で状態・辞書・候補が漏れない。
7. server kill、stale candidate、oversize、partial I/O、snapshot race から回復する。
8. fixed corpus の品質が現行 Mozc/Hazkey oracle に対して非劣化である。
9. key dispatch、preedit、candidate click、focus 切替を GTK、Qt、Electron で実機確認する。
10. Mozkey package の install、upgrade、uninstall、reinstall、consumer heartbeat と runtime identity を確認する。

Hazkey の installable rollback artifact は作らない。研究 branch/tag と評価 artifact は
比較根拠の再現用に保持するが、既定 package、service、設定 UI、runtime path には
含めない。

## Upstream maintenance

fork の `main` は Mozkey upstream に追随し、Grimodex/Linux 差分は独立 commit 群に保つ。
Fcitx adapter は `fcitx/mozc` の subtree identity、Linux Zenz は採用元 commit、Snapshot
Protocol は fixture digest を記録する。upstream 同期のたびに Linux build、project
contract、secure/multisession、quality corpus の順で gate を再実行する。
