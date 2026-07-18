# How to build Mozc on macOS

<!-- disableFinding(LINK_RELATIVE_G3DOC) -->

[![macOS](https://github.com/koyasi777/mozkey/actions/workflows/macos.yaml/badge.svg)](https://github.com/koyasi777/mozkey/actions/workflows/macos.yaml)

## Summary

If you are not sure what the following commands do, please check the
descriptions below and make sure the operations before running them.

```
git clone https://github.com/koyasi777/mozkey.git
cd mozkey/src

python3 build_tools/update_deps.py

# CMake is required to build both Qt and the bundled Zenz runtime.
python3 build_tools/build_qt.py --release --confirm_license

python3 ../tools/release/prepare_macos_zenz_runtime.py
bazelisk build package --config release_build
open bazel-bin/mac/Mozc.pkg
```

💡 With the above build steps, the target CPU architecture of the binaries in
`Mozc.pkg` is the same as the CPU architecture of the build environment. That
is, if you build Mozc on arm64 environment, `Mozc.pkg` contains arm64 binaries.
See the
["how to specify target CPU architectures"](#how-to-specify-target-cpu-architectures)
section below about how to do cross build.

💡 You can also download `Mozc.pkg` from GitHub Actions. Check
[Build with GitHub Actions](#build-with-github-actions) for details.

## Setup

### System Requirements

64-bit macOS 12 and later versions are supported.

### Software Requirements

Building on Mac requires the following software.

*   [Xcode](https://apps.apple.com/us/app/xcode/id497799835)
    *   Xcode 16.0 or later
    *   ⚠️Xcode Command Line Tools aren't sufficient.
*   [Bazelisk](https://github.com/bazelbuild/bazelisk)
    *   Bazelisk is a wrapper of [Bazel](https://bazel.build/) to use the
        specific version of Bazel.
    *   [src/.bazeliskrc](../src/.bazeliskrc) controls which version of Bazel is
        used.
*   Python 3.12 or later.
*   CMake 3.18.4 or later (to build Qt6 and the pinned Zenz runtime)

## Get the Code

You can download Mozc source code as follows:

```
git clone https://github.com/koyasi777/mozkey.git
cd mozkey/src
```

Hereafter you can do all the operations without changing directory.

### Check out additional build dependencies

```
python build_tools/update_deps.py
```

In this step, additional build dependencies will be downloaded.

*   [Ninja 1.11.0](https://github.com/ninja-build/ninja/releases/download/v1.11.0/ninja-mac.zip)
*   [Qt 6.9.1](https://download.qt.io/archive/qt/6.8/6.8.0/submodules/qtbase-everywhere-src-6.9.1.tar.xz)

You can specify `--noqt` option if you would like to use your own Qt binaries.

### Build Qt

```
python3 build_tools/build_qt.py --release --confirm_license
```

Drop `--confirm_license` option if you would like to manually confirm the Qt
license.

You can also specify `--debug` option to build debug version of Mozc.

```
python3 build_tools/build_qt.py --release --debug --confirm_license
```

You can also specify `--macos_cpus` option, which has the same semantics as the
[same name option in Bazel](https://bazel.build/reference/command-line-reference#flag--macos_cpus),
for cross-build including building a Universal macOS Binary.

```
# Building x86_64 binaries regardless of the host CPU architecture.
python3 build_tools/build_qt.py --release --debug --confirm_license --macos_cpus=x86_64

# Building Universal macOS Binary for both x86_64 and arm64.
python3 build_tools/build_qt.py --release --debug --confirm_license --macos_cpus=x86_64,arm64
```

You can skip this process if you have already installed Qt prebuilt binaries.

CMake is required by the Zenz runtime preparation step even when you use
prebuilt Qt binaries. If you use `brew`, you can install `cmake` as follows.

```
brew install cmake
```

--------------------------------------------------------------------------------

## Build with Bazel

### Build installer

First stage the pinned Universal Zenz runtime. This command downloads the
official pinned llama.cpp archive when it is not already cached, verifies its
SHA-256, and builds both `arm64` and `x86_64` runtime slices.

```
python3 ../tools/release/prepare_macos_zenz_runtime.py
bazelisk build package --config release_build
open bazel-bin/mac/Mozc.pkg
```

#### How to specify target CPU architectures

`--macos_cpus` controls the architecture of the Mozkey applications. Every
package variant embeds the same independently built Universal (`arm64` and
`x86_64`) Zenz scorer and `llama-server`, so the runtime preparation command
does not take `--macos_cpus`.

To build an Intel64 macOS binary regardless of the host CPU architecture.

```
python3 build_tools/build_qt.py --release --debug --confirm_license --macos_cpus=x86_64
python3 ../tools/release/prepare_macos_zenz_runtime.py
bazelisk build package --config release_build --macos_cpus=x86_64
open bazel-bin/mac/Mozc.pkg
```

To build a Universal macOS Binary both x86_64 and arm64.

```
python3 build_tools/build_qt.py --release --debug --confirm_license --macos_cpus=x86_64,arm64
python3 ../tools/release/prepare_macos_zenz_runtime.py
bazelisk build package --config release_build --macos_cpus=x86_64,arm64
open bazel-bin/mac/Mozc.pkg
```

### Unit tests

```
bazelisk test ... --build_tests_only -c dbg
```

See [build Mozc in Docker](build_mozc_in_docker.md#unittests) for details.

### Mozkey Grimodex desktop contract tests

The macOS workflow also runs the portable Protocol v1 fixtures, the secure
POSIX reader/registrar tests, IMKit context and renderer callback tests, and the
packaged Zenz runtime probes. See
[Grimodex Protocol v1 on Windows and macOS](grimodex_desktop_integration.md)
for the focused command and the security/lifecycle contract.

### Edit src/config.bzl

You can modify variables in `src/config.bzl` to fit your environment. Note: `~`
does not represent the home directory. The exact path should be specified (e.g.
`MACOS_QT_PATH = "/Users/mozc/myqt"`).

Tips: the following command makes the specified file untracked by Git.

```
git update-index --assume-unchanged src/config.bzl
```

This command reverts the above change.

```
git update-index --no-assume-unchanged src/config.bzl
```

--------------------------------------------------------------------------------

## Build with GitHub Actions

GitHub Actions steps are already set up in
[macos.yaml](../.github/workflows/macos.yaml). With that, you can build and
install Mozc with your own commit as follows.

1.  Fork https://github.com/koyasi777/mozkey to your GitHub repository.
2.  Push a new commit to your own fork.
3.  Click "Actions" tab on your fork.
4.  Wait until the action triggered with your commit succeeds.
5.  Download `Mozc.pkg` from the action result page.
6.  Install `Mozc.pkg`.

Files in the GitHub Actions page remain available up to 90 days.

You can also find Mozkey installers for macOS in the Mozkey repository. Please
keep in mind that Mozc is not an officially supported Google product, even if
downloaded from https://github.com/koyasi777/mozkey/.

1.  Sign in GitHub.
2.  Check
    [recent successful macOS runs](https://github.com/koyasi777/mozkey/actions/workflows/macos.yaml?query=is%3Asuccess)
    in the Mozkey repository.
3.  Find action in last 90 days and click it.
4.  Download `Mozc.pkg` from the action result page.

--------------------------------------------------------------------------------

## Build with GYP (deprecated):

⚠️ The GYP build is deprecated and no longer supported.

Please check the previous version for more information.
https://github.com/google/mozc/blob/3.33.6089/docs/build_mozc_in_osx.md#build-with-gyp-maintenance-mode
