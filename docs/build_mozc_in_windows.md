# How to build Mozc in Windows

<!-- disableFinding(LINK_RELATIVE_G3DOC) -->

[![Windows](https://github.com/koyasi777/mozkey/actions/workflows/windows.yaml/badge.svg)](https://github.com/koyasi777/mozkey/actions/workflows/windows.yaml)

## Summary

If you are unsure about what the following commands do, please review the
descriptions below to understand the operations before running them.

```
git clone https://github.com/koyasi777/mozkey.git
cd mozkey\src

python build_tools/update_deps.py
python ..\tools\release\prepare_windows_zenz_runtime.py --arch x64
python build_tools/build_qt.py --release --confirm_license
bazelisk build --config release_build package

python build_tools/open.py bazel-bin/win32/installer/Mozc64.msi
```

> [!TIP] You can also download `Mozc64.msi` from GitHub Actions. Check
> [Build with GitHub Actions](#build-with-github-actions) for details.

## Setup

### System Requirements

64-bit Windows 10 or later.

> [!IMPORTANT] Building Mozc on a Windows ARM64 environment is not yet supported
> ([#1296](https://github.com/google/mozc/issues/1296)).

### Software Requirements

Building Mozc on Windows requires the following software.

*   [Visual Studio 2022 Community Edition](https://visualstudio.microsoft.com/downloads/#visual-studio-community-2022)
    with the following components.
    *   Windows 11 SDK
    *   MSVC v143 - VS 2022 C++ x64/x86 build tools (Latest)
    *   C++ ATL for latest v143 build tools (x86 & x64)
*   Python 3.12 or later.
*   `.NET 6` or later (for `dotnet` command).
*   [Bazelisk](https://github.com/bazelbuild/bazelisk)

> [!TIP]
> Visual Studio 2026 Community Edition is also supported to build Mozc. When
> both VS 2022 and 2026 are installed, VS 2022 will be used.

> [!NOTE]
> Bazelisk is a wrapper of [Bazel](https://bazel.build) that allows you
> to use a specific version of Bazel.

### Download the repository from GitHub

```
git clone https://github.com/koyasi777/mozkey.git
cd mozkey\src
```

Hereafter you can do all the operations without changing directory.

### Check out additional build dependencies

```
python build_tools/update_deps.py
```

In this step, additional build dependencies will be downloaded, including:

*   [LLVM 20.1.1](https://github.com/llvm/llvm-project/releases/tag/llvmorg-20.1.1)
*   [MSYS2 2025-02-21](https://github.com/msys2/msys2-installer/releases/tag/2025-02-21)
*   [Ninja 1.13.2](https://github.com/ninja-build/ninja/releases/tag/v1.13.2)
*   [Qt 6.9.1](https://download.qt.io/archive/qt/6.8/6.8.0/submodules/qtbase-everywhere-src-6.9.1.tar.xz)
*   [.NET tools](../dotnet-tools.json)

## Build

### Build Qt

```
python build_tools/build_qt.py --release --confirm_license
```

If you would like to manually confirm the Qt license, omit the
`--confirm_license` option.

### Prepare the pinned Zenz runtime

The MSI never packages the historical root-level llama.cpp PE files. Build and
verify the architecture-specific static runtime from the checksum-pinned
official llama.cpp `b9637` archive (commit
`aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`) first:

```
python ..\tools\release\prepare_windows_zenz_runtime.py --arch x64
python ..\tools\release\prepare_windows_zenz_runtime.py --arch x64 --verify-only
```

The generated `runtime-manifest.json` records the exact binary digest, PE
machine, CMake options, and toolchain and is included in the MSI. The generated
directory is intentionally ignored by Git.

### Build Mozc

Assuming `bazelisk` is in your `%PATH%`, run the following command to build Mozc
for Windows.

```
bazelisk build --config oss_windows --config release_build package
```

#### Install Mozc

After building Mozc, run the following command to install it:

```
python build_tools/open.py bazel-bin/win32/installer/Mozc64.msi
```

#### Uninstall Mozc

To Uninstall Mozc, press <kbd>Win</kbd>+<kbd>R</kbd> to open the Run dialog and
type and type `ms-settings:appsfeatures-app`, run the following command in the
terminal:

```
start ms-settings:appsfeatures-app
```

Then, uninstall `Mozc` from the list of installed applications.

### Build `Mozc64.msi` for ARM64

To compile executables for ARM64, the following Visual Studio components also
need to be installed:

*   MSVC v143 - VS 2022 C++ ARM64/ARM64EC build tools (Latest)
*   C++ ATL for latest v143 build tools (ARM64/ARM64EC)

To build `Mozc64.msi` for ARM64, run the following commands:

```
python ..\tools\release\prepare_windows_zenz_runtime.py --arch arm64
python ..\tools\release\prepare_windows_zenz_runtime.py --arch arm64 --verify-only
python build_tools/build_qt.py --release --confirm_license --target_arch=arm64
bazelisk build --config oss_windows --config release_build package --platforms=//:windows-arm64
```

The runtime preparation command discovers `vcvarsall.bat`, imports its
`amd64_arm64` SDK environment, and then uses the checksum-pinned LLVM 20.1.1
and Ninja 1.13.2 installed by `update_deps.py`. It configures llama.cpp with the
pinned `cmake/arm64-windows-llvm.cmake` cross-toolchain; no separate Ninja or
LLVM installation is used.

### Build `Mozc64.msi` for both X64 and ARM64

To built a X64 installer that is also compatible with ARM64 machines, run the
following commands:

```
python ..\tools\release\prepare_windows_zenz_runtime.py --arch x64
python build_tools/build_qt.py --release --confirm_license
bazelisk build --config oss_windows --config release_build --config win_universal_installer package
```

The universal MSI keeps its Mozc server, scorer, and pinned Zenz runtime x64
and adds the ARM64/ARM64EC TIP bridge binaries; those x64 processes run through
Windows-on-ARM emulation. The native ARM64 MSI instead requires and verifies an
ARM64 Zenz runtime. Bazel selects between the generated inputs by target
platform, and the runtime verifier rejects a mismatched PE machine.

To build the above installer, the following Visual Studio components also need
to be installed:

*   MSVC v143 - VS 2022 C++ ARM64/ARM64EC build tools (Latest)
*   C++ ATL for latest v143 build tools (ARM64/ARM64EC)

## Bazel command examples

### Bazel User Guide

*   [Build programs with Bazel](https://bazel.build/run/build)
*   [Commands and Options](https://bazel.build/docs/user-manual)
*   [Write bazelrc configuration files](https://bazel.build/run/bazelrc)

### Run all tests

```
bazelisk test ... --config oss_windows --build_tests_only -c dbg
```

> [!NOTE] `...` means all targets under the current and subdirectories.

### Mozkey Grimodex desktop contract tests

The Windows workflow also runs the portable Protocol v1 fixtures, the secure
Windows reader/registrar tests, TSF context and edit-session tests, and renderer
callback provenance tests. See
[Grimodex Protocol v1 on Windows and macOS](grimodex_desktop_integration.md)
for the focused command and the security/lifecycle contract.

--------------------------------------------------------------------------------

## Build with GitHub Actions

GitHub Actions are already set up in
[windows.yaml](../.github/workflows/windows.yaml). With that, you can build and
install Mozc with your own commit as follows.

1.  Fork https://github.com/koyasi777/mozkey to your GitHub repository.
2.  Push a new commit to your own fork.
3.  Click "Actions" tab on your fork.
4.  Wait until the action triggered by your commit succeeds.
5.  Download `Mozc64.msi` from the action result page.
6.  Install `Mozc64.msi`.

Files on the GitHub Actions page remain available for up to 90 days.

You can also find Mozkey installers for Windows in the Mozkey repository.
Please keep in mind that Mozc is not an officially supported Google product,
even if downloaded from https://github.com/koyasi777/mozkey/.

1.  Sign in GitHub.
2.  Check
    [recent successful Windows runs](https://github.com/koyasi777/mozkey/actions/workflows/windows.yaml?query=is%3Asuccess)
    in the Mozkey repository.
3.  Find an action from the last 90 days and click it.
4.  Download `Mozc64.msi` from the action result page if you are using 64-bit
    Windows.

--------------------------------------------------------------------------------

## Build with GYP (deprecated):

⚠️ The GYP build is deprecated and no longer supported.

Please check the previous version for more information.
https://github.com/google/mozc/blob/3.33.6089/docs/build_mozc_in_windows.md#build-with-gyp-maintenance-mode
