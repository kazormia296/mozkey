# How to build Mozc on macOS

<!-- disableFinding(LINK_RELATIVE_G3DOC) -->

[![macOS](https://github.com/kazormia296/mozkey-ibg/actions/workflows/macos.yaml/badge.svg)](https://github.com/kazormia296/mozkey-ibg/actions/workflows/macos.yaml)

## Summary

If you are not sure what the following commands do, please check the
descriptions below and make sure the operations before running them.

```
git clone https://github.com/kazormia296/mozkey-ibg.git
cd mozkey-ibg/src

python3 build_tools/update_deps.py

# CMake is required to build both Qt and the bundled Zenz runtime.
python3 build_tools/build_qt.py --release --confirm_license --macos_cpus=arm64

python3 ../tools/release/prepare_macos_zenz_runtime.py
bazelisk build package --config release_build --macos_cpus=arm64
open bazel-bin/mac/MozkeyIbG.pkg
```

💡 Mozkey for macOS is distributed for Apple Silicon only. The application,
installer, Zenz scorer, and `llama-server` runtime are all arm64-only.

💡 You can also download `MozkeyIbG.pkg` from GitHub Actions. Check
[Build with GitHub Actions](#build-with-github-actions) for details.

## Setup

### System Requirements

Apple Silicon Macs running macOS 12 or later are supported.

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
git clone https://github.com/kazormia296/mozkey-ibg.git
cd mozkey-ibg/src
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
python3 build_tools/build_qt.py --release --confirm_license --macos_cpus=arm64
```

Drop `--confirm_license` option if you would like to manually confirm the Qt
license.

You can also specify `--debug` option to build debug version of Mozc.

```
python3 build_tools/build_qt.py --release --debug --confirm_license --macos_cpus=arm64
```

The explicit `--macos_cpus=arm64` option matches the Mozkey release contract.

You can skip this process if you have already installed Qt prebuilt binaries.

CMake is required by the Zenz runtime preparation step even when you use
prebuilt Qt binaries. If you use `brew`, you can install `cmake` as follows.

```
brew install cmake
```

--------------------------------------------------------------------------------

## Build with Bazel

### Build installer

First stage the pinned arm64 Zenz runtime. This command downloads the
official pinned llama.cpp archive when it is not already cached, verifies its
SHA-256, and builds the arm64 runtime.

```
python3 ../tools/release/prepare_macos_zenz_runtime.py
bazelisk build package --config release_build --macos_cpus=arm64
open bazel-bin/mac/MozkeyIbG.pkg
```

The runtime preparation command has no architecture option because its output
is intentionally fixed to arm64 with a macOS 12 deployment target. The package
build uses the matching explicit `--macos_cpus=arm64` contract.

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
[macos.yaml](../.github/workflows/macos.yaml). Pull requests and ordinary pushes
run the macOS test gate only; they do not build or upload a package. Installer
builds are called by the release workflow from a validated release tag.

1.  Fork https://github.com/kazormia296/mozkey-ibg to your GitHub repository.
2.  Update `src/version.bzl`, merge the tested change into `main`, and create an
    annotated `vX.Y.Z` tag with the same version.
3.  Push the tag and wait for the **Mozkey Release** workflow to succeed.
4.  Review the generated draft prerelease and its checksums.
5.  Download `MozkeyIbG_vX.Y.Z_macos_arm64.pkg` after publishing the release.

See [Releasing Mozkey](releasing.md) for the exact version, ancestry, release
notes, and rerun contract.

You can also find Mozkey installers for macOS in the Mozkey repository. Please
keep in mind that Mozc is not an officially supported Google product, even if
downloaded from https://github.com/kazormia296/mozkey-ibg/.

1.  Sign in GitHub.
2.  Open the repository's
    [Releases](https://github.com/kazormia296/mozkey-ibg/releases) page.
3.  Download the macOS arm64 package for the intended version.

--------------------------------------------------------------------------------

## Build with GYP (deprecated):

⚠️ The GYP build is deprecated and no longer supported.

Please check the previous version for more information.
https://github.com/google/mozc/blob/3.33.6089/docs/build_mozc_in_osx.md#build-with-gyp-maintenance-mode
