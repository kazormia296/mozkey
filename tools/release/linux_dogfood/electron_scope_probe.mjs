#!/usr/bin/node

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  closeSync,
  fchmodSync,
  fsyncSync,
  lstatSync,
  mkdtempSync,
  openSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  statSync,
  watch,
  writeFileSync,
  writeSync,
} from "node:fs";
import { createRequire } from "node:module";
import { homedir, userInfo } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function canonicalPath(name, expectedType) {
  const supplied = required(name);
  if (!path.isAbsolute(supplied)) {
    throw new Error(`${name} must be absolute`);
  }
  const resolved = path.resolve(supplied);
  if (lstatSync(resolved).isSymbolicLink()) {
    throw new Error(`${name} must not be a symlink`);
  }
  if (realpathSync(resolved) !== resolved) {
    throw new Error(
      `${name} must be a canonical path without symlink ancestors`,
    );
  }
  const metadata = statSync(resolved);
  if (
    (expectedType === "file" && !metadata.isFile()) ||
    (expectedType === "directory" && !metadata.isDirectory())
  ) {
    throw new Error(`${name} is not a ${expectedType}`);
  }
  return { path: resolved, metadata };
}

function sha256File(filename) {
  return createHash("sha256").update(readFileSync(filename)).digest("hex");
}

function gitBlobId(payload) {
  return createHash("sha1")
    .update(`blob ${payload.length}\0`)
    .update(payload)
    .digest("hex");
}

function snapshotTrackedFile(source, destination, expectedBlob, mode) {
  const payload = readFileSync(source);
  if (payload.length > 4 << 20 || gitBlobId(payload) !== expectedBlob) {
    throw new Error("tracked harness changed before its immutable snapshot");
  }
  const descriptor = openSync(destination, "wx", mode);
  try {
    writeFileSync(descriptor, payload);
    fsyncSync(descriptor);
    fchmodSync(descriptor, mode);
  } finally {
    closeSync(descriptor);
  }
  const metadata = lstatSync(destination);
  if (
    metadata.isSymbolicLink() ||
    !metadata.isFile() ||
    metadata.uid !== process.getuid() ||
    metadata.gid !== process.getgid() ||
    (metadata.mode & 0o777) !== mode ||
    realpathSync(destination) !== destination ||
    gitBlobId(readFileSync(destination)) !== expectedBlob
  ) {
    throw new Error("immutable tracked harness snapshot identity is invalid");
  }
  return Object.freeze({
    expectedBlob,
    identity: fileIdentity(destination),
    mode,
    path: destination,
  });
}

function startProtocolMutationGuard(root, fixture) {
  const projectId = fixture.project.project_id;
  const paths = [
    root,
    path.join(root, "projects"),
    path.join(root, "state.json"),
    path.join(root, "projects", `${projectId}.json`),
  ];
  const events = [];
  const watchers = [];
  try {
    for (const filename of paths) {
      const watcher = watch(filename, { persistent: false }, (eventType) => {
        events.push(eventType);
      });
      watcher.on("error", (error) => events.push(error));
      watchers.push(watcher);
    }
  } catch (error) {
    for (const watcher of watchers) watcher.close();
    throw error;
  }
  let closed = false;
  return {
    assertClean() {
      if (events.length !== 0) {
        throw new Error(
          "Protocol fixture was mutated during the Electron gate",
        );
      }
    },
    close() {
      if (closed) return;
      closed = true;
      for (const watcher of watchers) watcher.close();
    },
  };
}

function fileIdentity(filename) {
  const metadata = statSync(filename, { bigint: true });
  return {
    device: metadata.dev.toString(),
    inode: metadata.ino.toString(),
    mode: metadata.mode.toString(),
    size: metadata.size.toString(),
  };
}

function directoryIdentity(filename) {
  const metadata = statSync(filename, { bigint: true });
  return {
    device: metadata.dev.toString(),
    inode: metadata.ino.toString(),
    mode: metadata.mode.toString(),
  };
}

function sameIdentity(left, right) {
  return Object.keys(left).every((key) => left[key] === right[key]);
}

function expectedSha256(name, filename) {
  const expected = required(name);
  if (!/^[0-9a-f]{64}$/.test(expected)) {
    throw new Error(`${name} must be a lowercase SHA-256 digest`);
  }
  const actual = sha256File(filename);
  if (actual !== expected) {
    throw new Error(`${name} does not match the selected artifact`);
  }
  return actual;
}

function directoryManifest(directory) {
  const records = [];
  const visit = (current, relative) => {
    const entries = readdirSync(current, { withFileTypes: true }).sort((a, b) =>
      a.name.localeCompare(b.name, "en"),
    );
    for (const entry of entries) {
      if (/[\u0000-\u001f\u007f]/.test(entry.name)) {
        throw new Error("Electron build manifest rejects control characters");
      }
      const absolute = path.join(current, entry.name);
      const childRelative = relative ? `${relative}/${entry.name}` : entry.name;
      const metadata = lstatSync(absolute);
      if (metadata.isSymbolicLink()) {
        throw new Error("Electron build manifest must not contain symlinks");
      }
      if (metadata.isDirectory()) {
        records.push(
          JSON.stringify({
            mode: (metadata.mode & 0o777).toString(8),
            path: childRelative,
            type: "directory",
          }),
        );
        visit(absolute, childRelative);
      } else if (metadata.isFile()) {
        records.push(
          JSON.stringify({
            mode: (metadata.mode & 0o777).toString(8),
            path: childRelative,
            sha256: sha256File(absolute),
            size: metadata.size,
            type: "file",
          }),
        );
      } else {
        throw new Error("Electron build manifest contains a special file");
      }
      if (records.length > 20_000) {
        throw new Error("Electron build manifest exceeds its file cap");
      }
    }
  };
  visit(directory, "");
  return createHash("sha256").update(JSON.stringify(records)).digest("hex");
}

function canonicalRuntimePath(filename, expectedType, label) {
  if (!path.isAbsolute(filename)) {
    throw new Error(`${label} must be absolute`);
  }
  const resolved = path.resolve(filename);
  const linkMetadata = lstatSync(resolved);
  if (linkMetadata.isSymbolicLink() || realpathSync(resolved) !== resolved) {
    throw new Error(`${label} must be canonical without symlink ancestors`);
  }
  const metadata = statSync(resolved);
  if (
    (expectedType === "file" && !metadata.isFile()) ||
    (expectedType === "directory" && !metadata.isDirectory())
  ) {
    throw new Error(`${label} is not a ${expectedType}`);
  }
  return { metadata, path: resolved };
}

function developmentRuntimeManifest(repository) {
  const inputs = {
    mainRoot: path.join(repository, "dist-electron"),
    rendererRoot: path.join(repository, "dist"),
    nativeNode: path.join(
      repository,
      "electron/native/grimodex-node/grimodex-node.node",
    ),
    mcpSidecar: path.join(repository, "src-tauri/target/release/grimodex-mcp"),
    semanticRoot: path.join(repository, "src-tauri/resources/semantic"),
  };
  const mainRoot = canonicalRuntimePath(
    inputs.mainRoot,
    "directory",
    "development main root",
  );
  const rendererRoot = canonicalRuntimePath(
    inputs.rendererRoot,
    "directory",
    "development renderer root",
  );
  const nativeNode = canonicalRuntimePath(
    inputs.nativeNode,
    "file",
    "development native addon",
  );
  const mcpSidecar = canonicalRuntimePath(
    inputs.mcpSidecar,
    "file",
    "development MCP sidecar",
  );
  const semanticRoot = canonicalRuntimePath(
    inputs.semanticRoot,
    "directory",
    "development semantic root",
  );
  if ((nativeNode.metadata.mode & 0o111) === 0) {
    throw new Error("development native addon is not executable");
  }
  if ((mcpSidecar.metadata.mode & 0o111) === 0) {
    throw new Error("development MCP sidecar is not executable");
  }
  const fileRecord = (label, candidate) => ({
    label,
    mode: (candidate.metadata.mode & 0o777).toString(8),
    path: path.relative(repository, candidate.path),
    sha256: sha256File(candidate.path),
    size: candidate.metadata.size,
    type: "file",
  });
  const records = [
    {
      label: "dist-electron",
      path: path.relative(repository, mainRoot.path),
      sha256: directoryManifest(mainRoot.path),
      type: "directory",
    },
    {
      label: "dist",
      path: path.relative(repository, rendererRoot.path),
      sha256: directoryManifest(rendererRoot.path),
      type: "directory",
    },
    fileRecord("grimodex-node", nativeNode),
    fileRecord("grimodex-mcp", mcpSidecar),
    {
      label: "semantic",
      path: path.relative(repository, semanticRoot.path),
      sha256: directoryManifest(semanticRoot.path),
      type: "directory",
    },
  ];
  return {
    digest: createHash("sha256").update(JSON.stringify(records)).digest("hex"),
    mcpSidecar: mcpSidecar.path,
    paths: inputs,
  };
}

function packagedRuntimeManifest(bundleRoot) {
  const root = canonicalRuntimePath(
    bundleRoot,
    "directory",
    "packaged Electron bundle root",
  );
  return {
    digest: directoryManifest(root.path),
    identity: directoryIdentity(root.path),
    root: root.path,
  };
}

function expectedDirectoryManifest(pathName, digestName, repository) {
  const { path: directory } = canonicalPath(pathName, "directory");
  if (!directory.startsWith(`${repository}${path.sep}`)) {
    throw new Error(`${pathName} must be inside Grimodex`);
  }
  const expected = required(digestName);
  if (!/^[0-9a-f]{64}$/.test(expected)) {
    throw new Error(`${digestName} must be a lowercase SHA-256 digest`);
  }
  const actual = directoryManifest(directory);
  if (actual !== expected) {
    throw new Error(`${digestName} does not match the selected build tree`);
  }
  return { directory, digest: actual };
}

function loadReleaseFixture(filename) {
  const fixture = JSON.parse(readFileSync(filename, "utf8"));
  const expectedKeys = [
    "custom_value",
    "default_value",
    "project",
    "reading",
    "schema_version",
    "state",
  ];
  if (
    JSON.stringify(Object.keys(fixture).sort()) !== JSON.stringify(expectedKeys)
  ) {
    throw new Error("tracked release fixture fields changed");
  }
  const {
    reading,
    custom_value: custom,
    default_value: baseline,
    project,
  } = fixture;
  if (
    fixture.schema_version !== 1 ||
    typeof reading !== "string" ||
    !/^[a-z]+$/.test(reading) ||
    reading.length > 128 ||
    typeof custom !== "string" ||
    typeof baseline !== "string" ||
    !custom ||
    !baseline ||
    custom === baseline ||
    custom.trim() === reading ||
    baseline.trim() === reading ||
    !project ||
    !Array.isArray(project.entries) ||
    project.entries.length !== 1 ||
    project.entries[0].surface !== custom
  ) {
    throw new Error("tracked release fixture expectations are invalid");
  }
  return fixture;
}

const consumerRefreshWait = new Int32Array(new SharedArrayBuffer(4));

function stableConsumerEntries(consumers) {
  const temporaryPattern = /^\.fcitx5-mozkey-ibg\.[1-9][0-9]*\.[0-9]+\.tmp$/;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const entries = readdirSync(consumers).sort();
    if (JSON.stringify(entries) === JSON.stringify(["fcitx5-mozkey-ibg.json"])) {
      return entries;
    }
    const temporary = entries.filter((name) => temporaryPattern.test(name));
    if (
      temporary.length === 1 &&
      (entries.length === 1 || entries.length === 2) &&
      entries.every(
        (name) => name === "fcitx5-mozkey-ibg.json" || temporary.includes(name),
      )
    ) {
      Atomics.wait(consumerRefreshWait, 0, 0, 10);
      continue;
    }
    throw new Error("Protocol fixture consumers directory entries are invalid");
  }
  throw new Error("Protocol consumer heartbeat refresh did not settle");
}

function verifyProtocolRoot(root, fixture) {
  const metadata = statSync(root);
  if (
    metadata.uid !== process.getuid() ||
    (metadata.mode & 0o777) !== 0o700 ||
    !metadata.isDirectory()
  ) {
    throw new Error("Protocol fixture root identity is invalid");
  }
  if (
    JSON.stringify(readdirSync(root).sort()) !==
    JSON.stringify(["consumers", "projects", "state.json"])
  ) {
    throw new Error("Protocol fixture root entries are invalid");
  }
  const consumers = path.join(root, "consumers");
  const consumersMetadata = statSync(consumers);
  if (
    lstatSync(consumers).isSymbolicLink() ||
    consumersMetadata.uid !== process.getuid() ||
    (consumersMetadata.mode & 0o777) !== 0o700 ||
    !consumersMetadata.isDirectory()
  ) {
    throw new Error("Protocol fixture consumers directory is invalid");
  }
  const consumerEntries = stableConsumerEntries(consumers);
  const consumer = path.join(consumers, consumerEntries[0]);
  const consumerMetadata = statSync(consumer);
  if (
    lstatSync(consumer).isSymbolicLink() ||
    !consumerMetadata.isFile() ||
    consumerMetadata.uid !== process.getuid() ||
    (consumerMetadata.mode & 0o777) !== 0o600 ||
    consumerMetadata.nlink !== 1
  ) {
    throw new Error("Protocol fixture consumer marker identity is invalid");
  }
  const projects = path.join(root, "projects");
  const projectsMetadata = statSync(projects);
  if (
    lstatSync(projects).isSymbolicLink() ||
    projectsMetadata.uid !== process.getuid() ||
    (projectsMetadata.mode & 0o777) !== 0o700 ||
    !projectsMetadata.isDirectory()
  ) {
    throw new Error("Protocol fixture projects identity is invalid");
  }
  const projectId = fixture.project.project_id;
  if (
    typeof projectId !== "string" ||
    !/^[a-z0-9-]+$/.test(projectId) ||
    fixture.state.active_project_id !== projectId
  ) {
    throw new Error("Protocol fixture project identity is invalid");
  }
  const expectedFiles = [`${projectId}.json`];
  if (
    JSON.stringify(readdirSync(projects).sort()) !==
    JSON.stringify(expectedFiles)
  ) {
    throw new Error("Protocol fixture projects directory is not exact");
  }
  const statePath = path.join(root, "state.json");
  const projectPath = path.join(projects, `${projectId}.json`);
  for (const filename of [statePath, projectPath]) {
    const fileMetadata = statSync(filename);
    if (
      lstatSync(filename).isSymbolicLink() ||
      !fileMetadata.isFile() ||
      fileMetadata.uid !== process.getuid() ||
      (fileMetadata.mode & 0o077) !== 0
    ) {
      throw new Error("Protocol fixture document identity is invalid");
    }
  }
  if (
    JSON.stringify(JSON.parse(readFileSync(statePath, "utf8"))) !==
      JSON.stringify(fixture.state) ||
    JSON.stringify(JSON.parse(readFileSync(projectPath, "utf8"))) !==
      JSON.stringify(fixture.project)
  ) {
    throw new Error(
      "Protocol fixture documents differ from the tracked fixture",
    );
  }
  return {
    rootIdentity: directoryIdentity(root),
    stateSha256: sha256File(statePath),
    projectSha256: sha256File(projectPath),
  };
}

function runGit(repository, args) {
  const result = spawnSync("/usr/bin/git", ["-C", repository, ...args], {
    encoding: "utf8",
    timeout: 10_000,
    env: {
      GIT_CONFIG_GLOBAL: "/dev/null",
      GIT_CONFIG_NOSYSTEM: "1",
      GIT_OPTIONAL_LOCKS: "0",
      PATH: "/usr/bin:/bin",
      HOME: "/var/empty",
      LANG: "C.UTF-8",
      LC_ALL: "C.UTF-8",
    },
  });
  if (result.error || result.signal || result.status !== 0) {
    throw new Error(`git ${args[0]} failed for the selected Grimodex checkout`);
  }
  return result.stdout.trim();
}

function verifyTrackedFile(repository, filename, expectedMode) {
  const relative = path.relative(repository, filename);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("tracked dogfood input is outside the Mozkey checkout");
  }
  const index = runGit(repository, [
    "ls-files",
    "--stage",
    "--",
    relative,
  ]).split(/\s+/);
  const head = runGit(repository, ["ls-tree", "HEAD", "--", relative]).split(
    /\s+/,
  );
  if (
    index.length !== 4 ||
    index[0] !== expectedMode ||
    index[3] !== relative ||
    head.length !== 4 ||
    head[0] !== expectedMode ||
    head[1] !== "blob" ||
    head[2] !== index[1] ||
    head[3] !== relative
  ) {
    throw new Error("dogfood input is not the expected committed HEAD blob");
  }
  const working = runGit(repository, ["hash-object", "--", relative]);
  if (working !== index[1]) {
    throw new Error("dogfood input differs from its committed blob");
  }
  return index[1];
}

function procStartTime(directory) {
  const value = readFileSync(`${directory}/stat`, "utf8");
  const close = value.lastIndexOf(")");
  if (close < 0) throw new Error("malformed Fcitx /proc stat");
  const fields = value
    .slice(close + 1)
    .trim()
    .split(/\s+/);
  if (fields.length < 20 || !/^[0-9]+$/.test(fields[19])) {
    throw new Error("missing Fcitx start time");
  }
  return fields[19];
}

function procRecord(pid) {
  const directory = `/proc/${pid}`;
  const metadata = statSync(directory);
  if (metadata.uid !== process.getuid()) {
    const error = new Error("Electron process tree has the wrong owner");
    error.code = "ERR_PROCESS_OWNER";
    throw error;
  }
  const value = readFileSync(`${directory}/stat`, "utf8");
  const close = value.lastIndexOf(")");
  const fields = value
    .slice(close + 1)
    .trim()
    .split(/\s+/);
  if (
    close < 0 ||
    fields.length < 20 ||
    !/^[0-9]+$/.test(fields[1]) ||
    !/^[0-9]+$/.test(fields[2]) ||
    !/^[0-9]+$/.test(fields[3]) ||
    !/^[0-9]+$/.test(fields[19])
  ) {
    throw new Error("Electron process tree has malformed stat data");
  }
  return {
    executable: readlinkSync(`${directory}/exe`),
    procDevice: metadata.dev.toString(),
    procInode: metadata.ino.toString(),
    pid,
    ppid: Number(fields[1]),
    processGroup: Number(fields[2]),
    session: Number(fields[3]),
    startTime: fields[19],
  };
}

function sameProcessAlive(identity) {
  try {
    const actual = procRecord(identity.pid);
    return (
      actual.startTime === identity.startTime &&
      actual.procDevice === identity.procDevice &&
      actual.procInode === identity.procInode
    );
  } catch (error) {
    if (
      error?.code === "ENOENT" ||
      error?.code === "ESRCH" ||
      error?.code === "ERR_PROCESS_OWNER"
    ) {
      return false;
    }
    throw error;
  }
}

function processIdentityKey(identity) {
  return [
    identity.pid,
    identity.startTime,
    identity.procDevice,
    identity.procInode,
  ].join(":");
}

function currentUserProcesses() {
  const records = [];
  const uid = process.getuid();
  for (const name of readdirSync("/proc")) {
    if (!/^[1-9][0-9]*$/.test(name)) continue;
    try {
      if (statSync(`/proc/${name}`).uid !== uid) continue;
      records.push(procRecord(Number(name)));
    } catch (error) {
      if (
        error?.code === "ENOENT" ||
        error?.code === "ESRCH" ||
        error?.code === "EACCES" ||
        error?.code === "EPERM" ||
        error?.code === "ERR_PROCESS_OWNER"
      ) {
        continue;
      }
      throw error;
    }
  }
  return records;
}

function descendantProcessTree(seedRecords) {
  const records = currentUserProcesses();
  const selected = new Map();
  for (const seed of seedRecords) {
    if (sameProcessAlive(seed)) selected.set(seed.pid, seed);
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const record of records) {
      if (!selected.has(record.pid) && selected.has(record.ppid)) {
        selected.set(record.pid, record);
        changed = true;
      }
    }
  }
  return [...selected.values()];
}

function exactProcessGroup(processGroup, session) {
  return currentUserProcesses().filter(
    (record) =>
      record.processGroup === processGroup && record.session === session,
  );
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForProcessSetExit(refresh, milliseconds) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) {
    if (!refresh().some(sameProcessAlive)) return true;
    await pause(25);
  }
  return !refresh().some(sameProcessAlive);
}

function signalExactProcesses(records, signal, signaled = null) {
  for (const record of [...records].reverse()) {
    if (!sameProcessAlive(record)) continue;
    const key = processIdentityKey(record);
    if (signaled?.has(key)) continue;
    try {
      process.kill(record.pid, signal);
      signaled?.add(key);
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
  }
}

async function stopDynamicProcessSet(
  refresh,
  label,
  termGraceMilliseconds,
  killGraceMilliseconds = 3_000,
  signalNewDuringTerm = true,
) {
  const termSignaled = new Set();
  let firstPass = true;
  let deadline = Date.now() + termGraceMilliseconds;
  while (true) {
    const alive = refresh().filter(sameProcessAlive);
    if (alive.length === 0) return;
    if (firstPass || signalNewDuringTerm) {
      signalExactProcesses(alive, "SIGTERM", termSignaled);
    }
    firstPass = false;
    if (Date.now() >= deadline) break;
    await pause(25);
  }

  deadline = Date.now() + killGraceMilliseconds;
  while (true) {
    const alive = refresh().filter(sameProcessAlive);
    if (alive.length === 0) return;
    signalExactProcesses(alive, "SIGKILL");
    if (Date.now() >= deadline) {
      throw new Error(`${label} did not terminate`);
    }
    await pause(25);
  }
}

function installedFcitxIdentity() {
  const uid = process.getuid();
  const matches = [];
  for (const name of readdirSync("/proc")) {
    if (!/^[1-9][0-9]*$/.test(name)) continue;
    const directory = `/proc/${name}`;
    let metadata;
    try {
      metadata = statSync(directory);
    } catch (error) {
      if (error?.code === "ENOENT" || error?.code === "ESRCH") continue;
      throw error;
    }
    if (metadata.uid !== uid) continue;
    let executable;
    try {
      executable = readlinkSync(`${directory}/exe`);
    } catch (error) {
      if (error?.code === "ENOENT" || error?.code === "ESRCH") continue;
      if (error?.code === "EACCES" || error?.code === "EPERM") {
        let rawComm;
        try {
          rawComm = readFileSync(`${directory}/comm`);
        } catch (commError) {
          if (commError?.code === "ENOENT" || commError?.code === "ESRCH") {
            try {
              statSync(directory);
            } catch (refreshError) {
              if (
                refreshError?.code === "ENOENT" ||
                refreshError?.code === "ESRCH"
              ) {
                continue;
              }
              throw refreshError;
            }
          }
          throw new Error(
            "protected process comm is unreadable during Fcitx discovery",
            { cause: commError },
          );
        }
        if (
          rawComm.length < 2 ||
          rawComm.length > 16 ||
          rawComm.at(-1) !== 0x0a ||
          rawComm.subarray(0, -1).some((byte) => byte < 0x20 || byte > 0x7e)
        ) {
          throw new Error(
            "protected process comm is invalid during Fcitx discovery",
          );
        }
        if (rawComm.subarray(0, -1).toString("ascii") === "fcitx5") {
          throw new Error("an Fcitx candidate executable is unreadable", {
            cause: error,
          });
        }
        // comm is refusal-only evidence.  An unrelated protected process may
        // be skipped, but comm never establishes an executable identity.
        continue;
      }
      throw error;
    }
    if (executable !== "/usr/bin/fcitx5") continue;

    // From this point on, any read failure is a gate failure. Silently
    // dropping an exact candidate could make two Fcitx instances look unique.
    const environment = readFileSync(`${directory}/environ`, "utf8").split(
      "\0",
    );
    const entries = environment.filter((entry) =>
      entry.startsWith("MOZKEY_GRIMODEX_SCOPE="),
    );
    const protocolEntries = environment.filter((entry) =>
      entry.startsWith("GRIMODEX_IME_ROOT="),
    );
    const profileEntries = environment.filter((entry) =>
      entry.startsWith("XDG_CONFIG_HOME="),
    );
    const homeEntries = environment.filter((entry) =>
      entry.startsWith("HOME="),
    );
    if (
      entries.length > 1 ||
      protocolEntries.length !== 1 ||
      profileEntries.length !== 1 ||
      homeEntries.length !== 1
    ) {
      throw new Error("Fcitx dogfood environment is ambiguous");
    }
    matches.push({
      pid: Number(name),
      startTime: procStartTime(directory),
      scope:
        entries.length === 1
          ? entries[0].slice("MOZKEY_GRIMODEX_SCOPE=".length)
          : null,
      protocolRoot: protocolEntries[0].slice("GRIMODEX_IME_ROOT=".length),
      profileRoot: profileEntries[0].slice("XDG_CONFIG_HOME=".length),
      homeRoot: homeEntries[0].slice("HOME=".length),
    });
  }
  if (matches.length !== 1) {
    throw new Error(
      `expected exactly one installed Fcitx, got ${matches.length}`,
    );
  }
  return matches[0];
}

function assertFcitxIdentity(
  expected,
  scopeMode,
  unknownScopeValue,
  protocolRoot,
  profileRoot,
) {
  const actual = installedFcitxIdentity();
  const expectedScope =
    scopeMode === "default"
      ? null
      : scopeMode === "unknown"
        ? unknownScopeValue
        : scopeMode;
  if (actual.scope !== expectedScope) {
    throw new Error(
      "installed Fcitx scope environment does not match the gate",
    );
  }
  if (actual.protocolRoot !== protocolRoot) {
    throw new Error("installed Fcitx Protocol root does not match the gate");
  }
  if (actual.profileRoot !== profileRoot) {
    throw new Error("installed Fcitx profile root does not match the gate");
  }
  if (actual.homeRoot !== profileRoot) {
    throw new Error("installed Fcitx HOME does not match the fresh gate root");
  }
  if (
    expected &&
    (actual.pid !== expected.pid ||
      actual.startTime !== expected.startTime ||
      actual.scope !== expected.scope ||
      actual.protocolRoot !== expected.protocolRoot ||
      actual.profileRoot !== expected.profileRoot ||
      actual.homeRoot !== expected.homeRoot)
  ) {
    throw new Error("installed Fcitx identity changed during the gate");
  }
  return actual;
}

function sessionBusIdentity() {
  const runtime = `/run/user/${process.getuid()}`;
  const bus = `${runtime}/bus`;
  const runtimeMetadata = lstatSync(runtime);
  const busMetadata = lstatSync(bus);
  if (
    runtimeMetadata.isSymbolicLink() ||
    !runtimeMetadata.isDirectory() ||
    runtimeMetadata.uid !== process.getuid() ||
    (runtimeMetadata.mode & 0o777) !== 0o700 ||
    realpathSync(runtime) !== runtime ||
    busMetadata.isSymbolicLink() ||
    !busMetadata.isSocket() ||
    busMetadata.uid !== process.getuid() ||
    (busMetadata.mode & 0o777) !== 0o666 ||
    realpathSync(bus) !== bus
  ) {
    throw new Error("desktop D-Bus socket identity is invalid");
  }
  return {
    runtime: directoryIdentity(runtime),
    bus: fileIdentity(bus),
  };
}

function busctlCall(member, argument) {
  const busctl = "/usr/bin/busctl";
  const busctlMetadata = statSync(busctl);
  const nodeMetadata = statSync("/usr/bin/node");
  if (
    !busctlMetadata.isFile() ||
    (busctlMetadata.mode & 0o111) === 0 ||
    (busctlMetadata.mode & 0o022) !== 0 ||
    busctlMetadata.uid !== nodeMetadata.uid ||
    busctlMetadata.gid !== nodeMetadata.gid
  ) {
    throw new Error("system busctl identity is invalid");
  }
  const address = `unix:path=/run/user/${process.getuid()}/bus`;
  const result = spawnSync(
    busctl,
    [
      `--address=${address}`,
      "--no-pager",
      "call",
      "org.freedesktop.DBus",
      "/org/freedesktop/DBus",
      "org.freedesktop.DBus",
      member,
      "s",
      argument,
    ],
    {
      encoding: "utf8",
      env: {
        DBUS_SESSION_BUS_ADDRESS: address,
        LANG: "C.UTF-8",
        LC_ALL: "C.UTF-8",
        PATH: "/usr/bin:/bin",
        XDG_RUNTIME_DIR: `/run/user/${process.getuid()}`,
      },
      maxBuffer: 4096,
      timeout: 5_000,
    },
  );
  if (
    result.error ||
    result.signal ||
    result.status !== 0 ||
    result.stderr ||
    typeof result.stdout !== "string" ||
    result.stdout.length > 4096
  ) {
    throw new Error(`D-Bus owner query failed: ${member}`);
  }
  return result.stdout;
}

function verifyIbusOwner(fcitx, expected = null) {
  const before = sessionBusIdentity();
  const ownerOutput = busctlCall("GetNameOwner", "org.freedesktop.IBus");
  const ownerMatch = /^s "(:[0-9]+\.[0-9]+)"\n$/.exec(ownerOutput);
  if (!ownerMatch) {
    throw new Error("IBus well-known name owner response is invalid");
  }
  const owner = ownerMatch[1];
  const parseUnsigned = (member) => {
    const match = /^u ([0-9]+)\n$/.exec(busctlCall(member, owner));
    if (!match || !Number.isSafeInteger(Number(match[1]))) {
      throw new Error(`IBus owner ${member} response is invalid`);
    }
    return Number(match[1]);
  };
  const pid = parseUnsigned("GetConnectionUnixProcessID");
  const uid = parseUnsigned("GetConnectionUnixUser");
  const secondOwner = /^s "(:[0-9]+\.[0-9]+)"\n$/.exec(
    busctlCall("GetNameOwner", "org.freedesktop.IBus"),
  );
  const secondPid = parseUnsigned("GetConnectionUnixProcessID");
  const secondUid = parseUnsigned("GetConnectionUnixUser");
  const proc = `/proc/${pid}`;
  if (
    !secondOwner ||
    secondOwner[1] !== owner ||
    secondPid !== pid ||
    secondUid !== uid ||
    uid !== process.getuid() ||
    pid !== fcitx.pid ||
    statSync(proc).uid !== process.getuid() ||
    readlinkSync(`${proc}/exe`) !== "/usr/bin/fcitx5" ||
    procStartTime(proc) !== fcitx.startTime
  ) {
    throw new Error(
      "IBus well-known name is not owned by the exact Fcitx lifetime",
    );
  }
  const after = sessionBusIdentity();
  const actual = { owner, pid, startTime: fcitx.startTime, before, after };
  if (
    JSON.stringify(before) !== JSON.stringify(after) ||
    (expected !== null && JSON.stringify(actual) !== JSON.stringify(expected))
  ) {
    throw new Error(
      "IBus owner or session D-Bus identity changed during the gate",
    );
  }
  return actual;
}

function desktopEnvironment(userData) {
  const uid = process.getuid();
  const account = userInfo();
  const runtime = `/run/user/${uid}`;
  if (
    process.env.XDG_RUNTIME_DIR !== runtime ||
    realpathSync(runtime) !== runtime ||
    statSync(runtime).uid !== uid ||
    (statSync(runtime).mode & 0o777) !== 0o700
  ) {
    throw new Error("desktop runtime directory identity is invalid");
  }
  const bus = `${runtime}/bus`;
  if (
    process.env.DBUS_SESSION_BUS_ADDRESS !== `unix:path=${bus}` ||
    !statSync(bus).isSocket() ||
    statSync(bus).uid !== uid
  ) {
    throw new Error("desktop D-Bus identity is invalid");
  }
  const waylandName = process.env.WAYLAND_DISPLAY;
  if (!waylandName || waylandName.includes("/") || waylandName === ".") {
    throw new Error("canonical Wayland display is required");
  }
  const waylandSocket = `${runtime}/${waylandName}`;
  if (
    lstatSync(waylandSocket).isSymbolicLink() ||
    !statSync(waylandSocket).isSocket() ||
    statSync(waylandSocket).uid !== uid
  ) {
    throw new Error("Wayland display socket identity is invalid");
  }
  if (homedir() !== account.homedir || account.uid !== uid) {
    throw new Error("desktop account identity is inconsistent");
  }
  return {
    DBUS_SESSION_BUS_ADDRESS: `unix:path=${bus}`,
    DISPLAY: process.env.DISPLAY ?? "",
    GRIMODEX_USER_DATA_DIR: userData,
    GTK_IM_MODULE: "fcitx",
    HOME: account.homedir,
    LANG: "C.UTF-8",
    LC_ALL: "C.UTF-8",
    LOGNAME: account.username,
    PATH: "/usr/bin:/bin",
    QT_IM_MODULE: "fcitx",
    USER: account.username,
    WAYLAND_DISPLAY: waylandName,
    XDG_RUNTIME_DIR: runtime,
    XDG_SESSION_TYPE: "wayland",
    XMODIFIERS: "@im=fcitx",
  };
}

if (
  realpathSync(process.execPath) !== realpathSync("/usr/bin/node") ||
  (statSync("/usr/bin/node").mode & 0o022) !== 0
) {
  throw new Error("dogfood gate requires exact system Node.js");
}

if (
  process.argv.length === 4 &&
  [
    "--manifest-tree",
    "--manifest-packaged-runtime",
    "--manifest-development-runtime",
  ].includes(process.argv[2])
) {
  const candidate = path.resolve(process.argv[3]);
  if (
    !path.isAbsolute(process.argv[3]) ||
    lstatSync(candidate).isSymbolicLink() ||
    !statSync(candidate).isDirectory() ||
    realpathSync(candidate) !== candidate
  ) {
    throw new Error("manifest tree must be a canonical absolute directory");
  }
  const digest =
    process.argv[2] === "--manifest-development-runtime"
      ? developmentRuntimeManifest(candidate).digest
      : process.argv[2] === "--manifest-packaged-runtime"
        ? packagedRuntimeManifest(candidate).digest
        : directoryManifest(candidate);
  const label =
    process.argv[2] === "--manifest-tree" ? "tree_sha256" : "runtime_sha256";
  process.stdout.write(`RESULT:${label}=${digest}\n`);
  process.exit(0);
}
if (process.argv.length !== 2) {
  throw new Error("unexpected command-line arguments");
}

const kind = required("MOZKEY_DOGFOOD_ELECTRON_KIND");
if (kind !== "packaged" && kind !== "development") {
  throw new Error(
    "MOZKEY_DOGFOOD_ELECTRON_KIND must be packaged or development",
  );
}
const { path: executablePath, metadata: executableMetadata } = canonicalPath(
  "MOZKEY_DOGFOOD_ELECTRON_BINARY",
  "file",
);
if ((executableMetadata.mode & 0o111) === 0) {
  throw new Error("MOZKEY_DOGFOOD_ELECTRON_BINARY is not executable");
}
const binarySha256 = expectedSha256(
  "MOZKEY_DOGFOOD_ELECTRON_SHA256",
  executablePath,
);
const binaryIdentity = fileIdentity(executablePath);
const { path: grimodexRepository } = canonicalPath(
  "MOZKEY_DOGFOOD_GRIMODEX_REPOSITORY",
  "directory",
);
const expectedHead = required("MOZKEY_DOGFOOD_GRIMODEX_HEAD");
if (!/^[0-9a-f]{40}$/.test(expectedHead)) {
  throw new Error("MOZKEY_DOGFOOD_GRIMODEX_HEAD must be a full commit ID");
}
if (
  runGit(grimodexRepository, ["rev-parse", "--show-toplevel"]) !==
  grimodexRepository
) {
  throw new Error(
    "MOZKEY_DOGFOOD_GRIMODEX_REPOSITORY is not the worktree root",
  );
}
if (runGit(grimodexRepository, ["rev-parse", "HEAD"]) !== expectedHead) {
  throw new Error("Grimodex HEAD does not match the release gate");
}
if (
  runGit(grimodexRepository, ["status", "--porcelain", "--untracked-files=no"])
) {
  throw new Error("Grimodex tracked worktree is not clean");
}
const { path: artifactPath } = canonicalPath(
  "MOZKEY_DOGFOOD_ELECTRON_ARTIFACT",
  "file",
);
const artifactSha256 = expectedSha256(
  "MOZKEY_DOGFOOD_ELECTRON_ARTIFACT_SHA256",
  artifactPath,
);
const artifactIdentity = fileIdentity(artifactPath);
const scriptPath = realpathSync(fileURLToPath(import.meta.url));
const dogfoodDirectory = path.dirname(scriptPath);
const trackedSequenceHelper = path.join(
  dogfoodDirectory,
  "send_ime_sequence.sh",
);
const trackedSocketVerifier = path.join(
  dogfoodDirectory,
  "verify_ydotool_socket.py",
);
const trackedYdotoolRunner = path.join(
  dogfoodDirectory,
  "run_verified_ydotool.py",
);
const trackedSurfaceLocator = path.join(
  dogfoodDirectory,
  "atspi_surface_locator.py",
);
const trackedCandidateVerifier = path.join(
  dogfoodDirectory,
  "verify_installed_candidate.py",
);
const trackedFixture = path.join(dogfoodDirectory, "release_fixture.json");
const mozkeyRepository = realpathSync(
  path.resolve(dogfoodDirectory, "../../.."),
);
const trackedOfficialAttestationVerifier = path.join(
  mozkeyRepository,
  "tools/release/linux_build_attestation.py",
);
const trackedZenzNormalizer = path.join(
  mozkeyRepository,
  "tools/release/normalize_zenz_gguf.py",
);
const suppliedMozkeyRepository = canonicalPath(
  "MOZKEY_DOGFOOD_MOZKEY_REPOSITORY",
  "directory",
).path;
if (suppliedMozkeyRepository !== mozkeyRepository) {
  throw new Error("Mozkey repository is not this dogfood checkout");
}
const expectedMozkeyHead = required("MOZKEY_DOGFOOD_MOZKEY_HEAD");
if (!/^[0-9a-f]{40}$/.test(expectedMozkeyHead)) {
  throw new Error("MOZKEY_DOGFOOD_MOZKEY_HEAD must be a full commit ID");
}
if (
  runGit(mozkeyRepository, ["rev-parse", "--show-toplevel"]) !==
    mozkeyRepository ||
  runGit(mozkeyRepository, ["rev-parse", "HEAD"]) !== expectedMozkeyHead ||
  runGit(mozkeyRepository, ["status", "--porcelain", "--untracked-files=no"])
) {
  throw new Error("Mozkey dogfood checkout provenance is invalid");
}
const harnessBlobs = {
  electron: verifyTrackedFile(mozkeyRepository, scriptPath, "100755"),
  fixture: verifyTrackedFile(mozkeyRepository, trackedFixture, "100644"),
  helper: verifyTrackedFile(mozkeyRepository, trackedSequenceHelper, "100755"),
  socketVerifier: verifyTrackedFile(
    mozkeyRepository,
    trackedSocketVerifier,
    "100755",
  ),
  ydotoolRunner: verifyTrackedFile(
    mozkeyRepository,
    trackedYdotoolRunner,
    "100755",
  ),
  surfaceLocator: verifyTrackedFile(
    mozkeyRepository,
    trackedSurfaceLocator,
    "100755",
  ),
  candidateVerifier: verifyTrackedFile(
    mozkeyRepository,
    trackedCandidateVerifier,
    "100755",
  ),
  officialAttestationVerifier: verifyTrackedFile(
    mozkeyRepository,
    trackedOfficialAttestationVerifier,
    "100755",
  ),
  zenzNormalizer: verifyTrackedFile(
    mozkeyRepository,
    trackedZenzNormalizer,
    "100644",
  ),
};
const fixture = loadReleaseFixture(trackedFixture);
const expectedCustomValue = fixture.custom_value;
const expectedDefaultValue = fixture.default_value;
const reading = fixture.reading;
const protocolRoot = canonicalPath(
  "MOZKEY_DOGFOOD_PROTOCOL_ROOT",
  "directory",
).path;
const profileRoot = canonicalPath(
  "MOZKEY_DOGFOOD_PROFILE_ROOT",
  "directory",
).path;
let protocolEvidence;
const scopeMode = required("MOZKEY_DOGFOOD_SCOPE_MODE");
if (!["default", "all", "off", "unknown"].includes(scopeMode)) {
  throw new Error("MOZKEY_DOGFOOD_SCOPE_MODE is invalid");
}
const secureEnvironment = process.env.MOZKEY_DOGFOOD_SECURE;
if (secureEnvironment !== undefined && secureEnvironment !== "1") {
  throw new Error("MOZKEY_DOGFOOD_SECURE must be 1 when set");
}
const secure = secureEnvironment === "1";
const unknownScopeValue =
  scopeMode === "unknown"
    ? required("MOZKEY_DOGFOOD_UNKNOWN_SCOPE_VALUE")
    : null;
if (
  unknownScopeValue !== null &&
  ["", "all", "all-applications", "grimodex", "grimodex-only", "off"].includes(
    unknownScopeValue.trim().toLowerCase(),
  )
) {
  throw new Error("unknown scope value must be an exact unsupported value");
}
const fcitxIdentity = assertFcitxIdentity(
  null,
  scopeMode,
  unknownScopeValue,
  protocolRoot,
  profileRoot,
);
const ibusIdentity = verifyIbusOwner(fcitxIdentity);
const expectedCustomApplied =
  !secure &&
  (scopeMode === "all" || (scopeMode === "default" && kind === "packaged"));
const expectedValue = secure
  ? reading
  : expectedCustomApplied
    ? expectedCustomValue
    : expectedDefaultValue;
const expectedScope =
  scopeMode === "default"
    ? null
    : scopeMode === "unknown"
      ? unknownScopeValue
      : scopeMode;

const requireFromGrimodex = createRequire(
  path.join(grimodexRepository, "package.json"),
);
const playwrightPackageJson = realpathSync(
  requireFromGrimodex.resolve("playwright/package.json"),
);
const playwrightRoot = path.dirname(playwrightPackageJson);
const playwrightPackage = JSON.parse(
  readFileSync(playwrightPackageJson, "utf8"),
);
if (
  playwrightPackage.version !== "1.60.0" ||
  !playwrightRoot.startsWith(`${grimodexRepository}${path.sep}`)
) {
  throw new Error("resolved Playwright package provenance is invalid");
}
const expectedPlaywrightSha256 = required("MOZKEY_DOGFOOD_PLAYWRIGHT_SHA256");
if (!/^[0-9a-f]{64}$/.test(expectedPlaywrightSha256)) {
  throw new Error("MOZKEY_DOGFOOD_PLAYWRIGHT_SHA256 is invalid");
}
const playwrightSha256 = directoryManifest(playwrightRoot);
if (playwrightSha256 !== expectedPlaywrightSha256) {
  throw new Error("resolved Playwright package tree does not match its digest");
}
const { _electron: electron } = requireFromGrimodex("playwright");

const launchArguments = ["--force-renderer-accessibility"];
let developmentMainTree = null;
let developmentRendererTree = null;
if (kind === "development") {
  const { path: developmentMain } = canonicalPath(
    "MOZKEY_DOGFOOD_ELECTRON_MAIN",
    "file",
  );
  if (!developmentMain.startsWith(`${grimodexRepository}${path.sep}`)) {
    throw new Error("development Electron main must be inside Grimodex");
  }
  if (developmentMain !== artifactPath) {
    throw new Error("development Electron main must be the attested artifact");
  }
  developmentMainTree = expectedDirectoryManifest(
    "MOZKEY_DOGFOOD_ELECTRON_MAIN_ROOT",
    "MOZKEY_DOGFOOD_ELECTRON_MAIN_ROOT_SHA256",
    grimodexRepository,
  );
  developmentRendererTree = expectedDirectoryManifest(
    "MOZKEY_DOGFOOD_ELECTRON_RENDERER_ROOT",
    "MOZKEY_DOGFOOD_ELECTRON_RENDERER_SHA256",
    grimodexRepository,
  );
  if (
    !developmentMain.startsWith(`${developmentMainTree.directory}${path.sep}`)
  ) {
    throw new Error("development main is outside its attested build tree");
  }
  if (developmentMainTree.directory !== path.dirname(developmentMain)) {
    throw new Error("development main root is not the exact output directory");
  }
  if (
    developmentMainTree.directory !==
    path.join(grimodexRepository, "dist-electron")
  ) {
    throw new Error("development main root is not Grimodex dist-electron");
  }
  if (
    developmentRendererTree.directory !==
      path.resolve(path.dirname(developmentMain), "../dist") ||
    developmentRendererTree.directory !== path.join(grimodexRepository, "dist")
  ) {
    throw new Error("development renderer root is not the tree loaded by main");
  }
  launchArguments.push(developmentMain);
} else if (
  !artifactPath.startsWith(`${path.dirname(executablePath)}${path.sep}`)
) {
  throw new Error(
    "packaged Electron artifact must be inside the selected bundle",
  );
}

const expectedRuntimeSha256 = required(
  "MOZKEY_DOGFOOD_ELECTRON_RUNTIME_SHA256",
);
if (!/^[0-9a-f]{64}$/.test(expectedRuntimeSha256)) {
  throw new Error("MOZKEY_DOGFOOD_ELECTRON_RUNTIME_SHA256 is invalid");
}
const runtimeEvidence =
  kind === "development"
    ? { kind, ...developmentRuntimeManifest(grimodexRepository) }
    : { kind, ...packagedRuntimeManifest(path.dirname(executablePath)) };
if (runtimeEvidence.digest !== expectedRuntimeSha256) {
  throw new Error("Electron composite runtime does not match its digest");
}

const runtimeRoot = `/run/user/${process.getuid()}`;
const ydotooldPid = required("MOZKEY_DOGFOOD_YDOTOOLD_PID");
const ydotoolSocket = required("YDOTOOL_SOCKET");
const baseEnvironment = desktopEnvironment("");
let application;
let launchPromise;
let electronRootIdentity;
const electronTrackedProcesses = new Map();
let activeHelper;
let userData;
let userDataIdentity;
let harnessSnapshotDirectory;
let harnessSnapshotDirectoryIdentity;
const harnessSnapshots = new Map();
let snapshotSequenceHelper;
let snapshotYdotoolRunner;
let snapshotSurfaceLocator;
let snapshotCandidateVerifier;
let snapshotOfficialAttestationVerifier;
let protocolMutationGuard;
let profileEvidence;
let candidateEvidence;
let surfaceEvidence;
let helperEnvironment;
let resultPayload;
let watchdog;
let applicationCleanupPromise;
let evidenceCleanupPromise;
let cleanupPromise;
let terminationPromise;
let terminationRequested = false;
const gateProcessIdentity = procRecord(process.pid);
const preLaunchDescendantKeys = new Set(
  descendantProcessTree([gateProcessIdentity])
    .filter((record) => record.pid !== process.pid)
    .map(processIdentityKey),
);

function verifyElectronRuntimeEvidence() {
  if (runtimeEvidence.kind === "packaged") {
    const actual = packagedRuntimeManifest(runtimeEvidence.root);
    if (
      actual.root !== runtimeEvidence.root ||
      !sameIdentity(runtimeEvidence.identity, actual.identity) ||
      actual.digest !== runtimeEvidence.digest
    ) {
      throw new Error("packaged Electron runtime changed during the gate");
    }
    return;
  }
  const actual = developmentRuntimeManifest(grimodexRepository);
  if (
    actual.digest !== runtimeEvidence.digest ||
    actual.mcpSidecar !== runtimeEvidence.mcpSidecar
  ) {
    throw new Error("development Electron runtime changed during the gate");
  }
}

function rememberHarnessSnapshot(name, snapshot) {
  if (harnessSnapshots.has(name)) {
    throw new Error("duplicate immutable harness snapshot name");
  }
  harnessSnapshots.set(name, snapshot);
  return snapshot;
}

function verifyHarnessSnapshotDirectory() {
  if (!harnessSnapshotDirectory || !harnessSnapshotDirectoryIdentity) {
    throw new Error("immutable harness snapshot directory is unavailable");
  }
  const metadata = lstatSync(harnessSnapshotDirectory);
  if (
    metadata.isSymbolicLink() ||
    !metadata.isDirectory() ||
    metadata.uid !== process.getuid() ||
    metadata.gid !== process.getgid() ||
    (metadata.mode & 0o777) !== 0o500 ||
    realpathSync(harnessSnapshotDirectory) !== harnessSnapshotDirectory ||
    !sameIdentity(
      harnessSnapshotDirectoryIdentity,
      directoryIdentity(harnessSnapshotDirectory),
    )
  ) {
    throw new Error("immutable harness snapshot directory changed");
  }
}

function verifyHarnessSnapshot(snapshot) {
  verifyHarnessSnapshotDirectory();
  if (path.dirname(snapshot.path) !== harnessSnapshotDirectory) {
    throw new Error("immutable harness snapshot escaped its private directory");
  }
  const metadata = lstatSync(snapshot.path);
  if (
    metadata.isSymbolicLink() ||
    !metadata.isFile() ||
    metadata.uid !== process.getuid() ||
    metadata.gid !== process.getgid() ||
    (metadata.mode & 0o777) !== snapshot.mode ||
    realpathSync(snapshot.path) !== snapshot.path ||
    !sameIdentity(snapshot.identity, fileIdentity(snapshot.path)) ||
    gitBlobId(readFileSync(snapshot.path)) !== snapshot.expectedBlob
  ) {
    throw new Error("immutable harness snapshot changed during the gate");
  }
}

function verifyHarnessSnapshots(names) {
  for (const name of names) {
    const snapshot = harnessSnapshots.get(name);
    if (!snapshot) {
      throw new Error(`immutable harness snapshot is missing: ${name}`);
    }
    verifyHarnessSnapshot(snapshot);
  }
}

function rememberProcesses(target, records) {
  for (const record of records) {
    target.set(processIdentityKey(record), record);
  }
}

function refreshElectronProcesses() {
  if (electronTrackedProcesses.size === 0) return [];
  rememberProcesses(
    electronTrackedProcesses,
    descendantProcessTree([...electronTrackedProcesses.values()]),
  );
  return [...electronTrackedProcesses.values()];
}

function discoverUnclaimedElectronProcesses() {
  const candidates = descendantProcessTree([gateProcessIdentity]).filter(
    (record) =>
      record.pid !== process.pid &&
      record.executable === executablePath &&
      !preLaunchDescendantKeys.has(processIdentityKey(record)),
  );
  rememberProcesses(electronTrackedProcesses, candidates);
  return refreshElectronProcesses();
}

function refreshHelperProcesses(run) {
  if (!run.processGroup || !run.session) return [...run.tracked.values()];
  rememberProcesses(
    run.tracked,
    exactProcessGroup(run.processGroup, run.session),
  );
  return [...run.tracked.values()];
}

async function ensureHelperStopped(run) {
  if (run.stopPromise) return run.stopPromise;
  run.stopPromise = (async () => {
    if (!run.rootIdentity && run.readyPromise) {
      try {
        await Promise.race([
          run.readyPromise,
          pause(2_000).then(() => {
            throw new Error("IME sequence helper startup timed out");
          }),
        ]);
      } catch {
        if (refreshHelperProcesses(run).some(sameProcessAlive)) {
          await stopDynamicProcessSet(
            () => refreshHelperProcesses(run),
            "IME sequence helper process group",
            35_000,
            3_000,
            false,
          );
          return;
        }
        if (run.child.pid) {
          try {
            run.child.kill("SIGKILL");
          } catch (error) {
            if (error?.code !== "ESRCH") throw error;
          }
        }
        if (run.completionPromise) {
          const stopped = await Promise.race([
            run.completionPromise.then(() => true),
            pause(2_000).then(() => false),
          ]);
          if (!stopped) {
            throw new Error(
              "IME sequence helper startup process remained alive",
            );
          }
        }
        return;
      }
    }
    await stopDynamicProcessSet(
      () => refreshHelperProcesses(run),
      "IME sequence helper process group",
      35_000,
      3_000,
      false,
    );
  })();
  return run.stopPromise;
}

async function runSequenceHelper(
  executable,
  arguments_,
  environment,
  timeoutMilliseconds = 30_000,
) {
  const child = spawn(executable, arguments_, {
    detached: true,
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const run = {
    child,
    completionPromise: null,
    processGroup: child.pid ?? null,
    readyPromise: null,
    rootIdentity: null,
    session: child.pid ?? null,
    stopPromise: null,
    tracked: new Map(),
  };
  activeHelper = run;

  const stdout = [];
  const stderr = [];
  let outputBytes = 0;
  let guardReject;
  let guardFailed = false;
  const guard = new Promise((_, reject) => {
    guardReject = reject;
  });
  const failGuard = (error) => {
    if (guardFailed) return;
    guardFailed = true;
    guardReject(error);
  };
  const capture = (target) => (chunk) => {
    outputBytes += chunk.length;
    if (outputBytes > 1 << 16) {
      failGuard(new Error("IME sequence output exceeded its cap"));
      return;
    }
    target.push(chunk);
  };
  child.stdout.on("data", capture(stdout));
  child.stderr.on("data", capture(stderr));

  const spawned = new Promise((resolve, reject) => {
    child.once("spawn", resolve);
    child.once("error", reject);
  });
  const completed = new Promise((resolve) => {
    child.once("close", (code, signal) => resolve({ code, signal }));
    child.once("error", (error) => resolve({ error }));
  });
  run.completionPromise = completed;
  run.readyPromise = spawned.then(() => {
    run.rootIdentity = procRecord(child.pid);
    if (
      run.rootIdentity.processGroup !== run.rootIdentity.pid ||
      run.rootIdentity.session !== run.rootIdentity.pid
    ) {
      throw new Error("IME sequence helper did not enter its private session");
    }
    run.processGroup = run.rootIdentity.processGroup;
    run.session = run.rootIdentity.session;
    rememberProcesses(run.tracked, [run.rootIdentity]);
    refreshHelperProcesses(run);
  });
  const guardedCompletion = Promise.race([completed, guard]);
  const timer = setTimeout(
    () => failGuard(new Error("IME sequence timed out")),
    timeoutMilliseconds,
  );

  let failure;
  let exit;
  let residue = false;
  try {
    await run.readyPromise;
    exit = await guardedCompletion;
    if (exit.error) throw exit.error;
    if (exit.signal || exit.code !== 0) {
      throw new Error(
        `IME sequence failed with status ${exit.code} signal ${exit.signal ?? "none"}`,
      );
    }
  } catch (error) {
    failure = error;
  } finally {
    clearTimeout(timer);
    try {
      residue = refreshHelperProcesses(run).some(sameProcessAlive);
      if (residue) await ensureHelperStopped(run);
    } catch (error) {
      failure ??= error;
    }
    if (activeHelper === run) activeHelper = null;
  }

  if (!failure && residue) {
    failure = new Error("IME sequence helper left process residue");
  }
  if (failure) throw failure;
  return {
    stderr: Buffer.concat(stderr).toString("utf8"),
    stdout: Buffer.concat(stdout).toString("utf8"),
  };
}

function parseElectronSurfaceTranscript(transcript) {
  if (transcript.stderr || Buffer.byteLength(transcript.stdout) > 4096) {
    throw new Error("AT-SPI surface locator output exceeded its exact contract");
  }
  const lines = transcript.stdout.split("\n");
  if (lines.length !== 2 || lines[1] !== "" || !lines[0].startsWith("SURFACE:")) {
    throw new Error("AT-SPI surface locator transcript was not exact");
  }
  let evidence;
  try {
    evidence = JSON.parse(lines[0].slice("SURFACE:".length));
  } catch {
    throw new Error("AT-SPI surface locator evidence is not valid JSON");
  }
  const expectedKeys = [
    "accessiblePid",
    "accessibleStartTime",
    "clickX",
    "clickY",
    "height",
    "ownerUid",
    "role",
    "schemaVersion",
    "targetPid",
    "targetStartTime",
    "title",
    "toolkit",
    "toolkitName",
    "width",
    "x",
    "y",
  ];
  if (
    !evidence ||
    Array.isArray(evidence) ||
    JSON.stringify(Object.keys(evidence).sort()) !== JSON.stringify(expectedKeys)
  ) {
    throw new Error("AT-SPI surface evidence fields changed");
  }
  const integerKeys = [
    "accessiblePid",
    "clickX",
    "clickY",
    "height",
    "ownerUid",
    "schemaVersion",
    "targetPid",
    "width",
    "x",
    "y",
  ];
  if (integerKeys.some((key) => !Number.isSafeInteger(evidence[key]))) {
    throw new Error("AT-SPI surface integer evidence is invalid");
  }
  const expectedTitle = secure
    ? "Mozkey Electron Password Probe"
    : "Mozkey Electron Probe";
  if (
    evidence.schemaVersion !== 1 ||
    evidence.toolkit !== "electron" ||
    !["chromium", "electron"].includes(evidence.toolkitName) ||
    evidence.targetPid !== electronRootIdentity.pid ||
    evidence.targetStartTime !== electronRootIdentity.startTime ||
    evidence.ownerUid !== process.getuid() ||
    (secure
      ? evidence.role !== "password text"
      : !["entry", "text"].includes(evidence.role)) ||
    evidence.title !== expectedTitle ||
    !/^[1-9][0-9]*$/.test(evidence.accessibleStartTime)
  ) {
    throw new Error("AT-SPI Electron surface owner, role, or title mismatch");
  }
  refreshElectronProcesses();
  const surfaceProcess = [...electronTrackedProcesses.values()].find(
    (record) =>
      record.pid === evidence.accessiblePid &&
      record.startTime === evidence.accessibleStartTime,
  );
  if (
    !surfaceProcess ||
    !sameProcessAlive(surfaceProcess) ||
    surfaceProcess.startTime !== evidence.accessibleStartTime ||
    surfaceProcess.executable !== executablePath ||
    readlinkSync(`/proc/${surfaceProcess.pid}/exe`) !== executablePath ||
    sha256File(`/proc/${surfaceProcess.pid}/exe`) !== binarySha256
  ) {
    throw new Error("AT-SPI Electron surface is not an attested live descendant");
  }
  if (
    evidence.width < 1 ||
    evidence.width > 32768 ||
    evidence.height < 1 ||
    evidence.height > 32768 ||
    evidence.x < -131072 ||
    evidence.x > 131072 ||
    evidence.y < -131072 ||
    evidence.y > 131072 ||
    evidence.x + evidence.width > 131072 ||
    evidence.y + evidence.height > 131072 ||
    evidence.clickX < -131072 ||
    evidence.clickX > 131072 ||
    evidence.clickY < -131072 ||
    evidence.clickY > 131072 ||
    evidence.clickX !== evidence.x + Math.floor(evidence.width / 2) ||
    evidence.clickY !== evidence.y + Math.floor(evidence.height / 2)
  ) {
    throw new Error("AT-SPI Electron surface screen extents are invalid");
  }
  return {
    ...evidence,
    procDevice: surfaceProcess.procDevice,
    procInode: surfaceProcess.procInode,
  };
}

async function locateElectronSurface(requireFocused = false) {
  verifyHarnessSnapshots(["surfaceLocator"]);
  const arguments_ = [
    "-I",
    realpathSync(snapshotSurfaceLocator.path),
    "--pid",
    String(electronRootIdentity.pid),
    "--start-time",
    electronRootIdentity.startTime,
    "--executable",
    executablePath,
    "--toolkit",
    "electron",
    "--timeout-seconds",
    "10",
  ];
  if (secure) arguments_.push("--secure");
  if (requireFocused) arguments_.push("--require-focused");
  const transcript = await runSequenceHelper(
    "/usr/bin/python3",
    arguments_,
    helperEnvironment,
    15_000,
  );
  verifyHarnessSnapshots(["surfaceLocator"]);
  return parseElectronSurfaceTranscript(transcript);
}

async function runVerifiedPointerCommand(arguments_) {
  verifyHarnessSnapshots(["socketVerifier", "ydotoolRunner"]);
  const transcript = await runSequenceHelper(
    "/usr/bin/python3",
    [
      "-I",
      realpathSync(snapshotYdotoolRunner.path),
      "--socket",
      ydotoolSocket,
      "--pid",
      ydotooldPid,
      "--timeout-seconds",
      "10",
      "--",
      ...arguments_,
    ],
    helperEnvironment,
    15_000,
  );
  verifyHarnessSnapshots(["socketVerifier", "ydotoolRunner"]);
  if (
    transcript.stderr ||
    transcript.stdout !== "ydotool socket verified: exact_private_owner\n"
  ) {
    throw new Error("verified Electron pointer command transcript was not exact");
  }
}

async function focusElectronSurface() {
  const before = await locateElectronSurface();
  await runVerifiedPointerCommand([
    "mousemove",
    "--absolute",
    String(before.clickX),
    String(before.clickY),
  ]);
  const atPointer = await locateElectronSurface();
  if (JSON.stringify(atPointer) !== JSON.stringify(before)) {
    throw new Error("AT-SPI Electron surface changed before the verified click");
  }
  await runVerifiedPointerCommand(["click", "0xC0"]);
  const focused = await locateElectronSurface(true);
  if (JSON.stringify(focused) !== JSON.stringify(before)) {
    throw new Error("AT-SPI Electron surface changed while receiving focus");
  }
  return focused;
}

async function runInstalledCandidateVerifier(profileOnly = false) {
  const candidateSnapshotNames = [
    "candidateVerifier",
    "officialAttestationVerifier",
    "zenzNormalizer",
  ];
  const candidateArguments = [
    "-I",
    snapshotCandidateVerifier.path,
    "--repository-root",
    mozkeyRepository,
    "--layout",
    "archlinux-x86_64",
    "--attestation",
    path.join(
      mozkeyRepository,
      "dist/linux/archlinux-x86_64/build-attestation.json",
    ),
    "--official-verifier",
    snapshotOfficialAttestationVerifier.path,
    "--fcitx-pid",
    String(fcitxIdentity.pid),
    "--protocol-root",
    protocolRoot,
    "--profile-root",
    profileRoot,
    "--scope-kind",
    expectedScope === null ? "absent" : "value",
  ];
  if (expectedScope !== null) {
    candidateArguments.push("--scope-value", expectedScope);
  }
  if (profileOnly) {
    candidateArguments.push("--profile-only");
  }

  verifyHarnessSnapshots(candidateSnapshotNames);
  let candidateVerification;
  try {
    candidateVerification = await runSequenceHelper(
      "/usr/bin/python3",
      candidateArguments,
      {
        GIT_CONFIG_GLOBAL: "/dev/null",
        GIT_CONFIG_NOSYSTEM: "1",
        GIT_OPTIONAL_LOCKS: "0",
        HOME: "/var/empty",
        LANG: "C.UTF-8",
        LC_ALL: "C.UTF-8",
        PATH: "/usr/bin:/bin",
      },
      120_000,
    );
  } finally {
    verifyHarnessSnapshots(candidateSnapshotNames);
  }

  const candidateLines = candidateVerification.stdout.split("\n");
  if (
    candidateVerification.stderr ||
    candidateLines.length !== 2 ||
    candidateLines[1] !== "" ||
    !candidateLines[0].startsWith("RESULT:")
  ) {
    throw new Error("installed candidate verifier transcript was not exact");
  }
  const evidence = JSON.parse(candidateLines[0].slice("RESULT:".length));
  const expectedCandidateKeys = [
    "addonSha256",
    "attestationSha256",
    "consumerSha256",
    "fcitxPid",
    "fcitxProfileSha256",
    "fcitxStartTime",
    "gitHead",
    "layout",
    "profileMarkerSha256",
    "profileRootSha256",
    "protocolRootSha256",
    "result",
    "schemaVersion",
    "scope",
    "serverPid",
    "serverSha256",
    "serverStartTime",
  ];
  if (
    !evidence ||
    JSON.stringify(Object.keys(evidence).sort()) !==
      JSON.stringify(expectedCandidateKeys) ||
    evidence.result !== (profileOnly ? "profile-pass" : "pass") ||
    evidence.schemaVersion !== "mozkey.linux_build_attestation.v2" ||
    evidence.gitHead !== expectedMozkeyHead ||
    evidence.layout !== "archlinux-x86_64" ||
    evidence.fcitxPid !== fcitxIdentity.pid ||
    evidence.fcitxStartTime !== fcitxIdentity.startTime ||
    evidence.scope !== expectedScope ||
    typeof evidence.consumerSha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(evidence.consumerSha256) ||
    evidence.profileRootSha256 !==
      createHash("sha256").update(profileRoot).digest("hex") ||
    typeof evidence.fcitxProfileSha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(evidence.fcitxProfileSha256) ||
    typeof evidence.profileMarkerSha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(evidence.profileMarkerSha256) ||
    evidence.protocolRootSha256 !==
      createHash("sha256").update(protocolRoot).digest("hex") ||
    (profileOnly
      ? evidence.serverPid !== null || evidence.serverStartTime !== null
      : !Number.isSafeInteger(evidence.serverPid) ||
        evidence.serverPid <= 1 ||
        typeof evidence.serverStartTime !== "string" ||
        !/^[0-9]+$/.test(evidence.serverStartTime)) ||
    !/^[0-9a-f]{64}$/.test(evidence.attestationSha256) ||
    !/^[0-9a-f]{64}$/.test(evidence.addonSha256) ||
    !/^[0-9a-f]{64}$/.test(evidence.serverSha256)
  ) {
    throw new Error("installed candidate verifier result identity mismatch");
  }
  return evidence;
}

function stableCandidateEvidence(evidence) {
  return Object.fromEntries(
    Object.entries(evidence).filter(([key]) => key !== "consumerSha256"),
  );
}

function assertCandidateMatchesProfilePreflight(profile, candidate) {
  for (const key of [
    "addonSha256",
    "attestationSha256",
    "fcitxPid",
    "fcitxStartTime",
    "gitHead",
    "layout",
    "fcitxProfileSha256",
    "profileMarkerSha256",
    "profileRootSha256",
    "protocolRootSha256",
    "schemaVersion",
    "scope",
    "serverSha256",
  ]) {
    if (profile?.[key] !== candidate?.[key]) {
      throw new Error("candidate identity changed after profile preflight");
    }
  }
}

async function closeApplicationBounded(candidate) {
  let timer;
  try {
    await Promise.race([
      candidate.close(),
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error("Electron application close timed out")),
          15_000,
        );
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function removeExactUserData() {
  if (!userData) return;
  let metadata;
  try {
    metadata = lstatSync(userData);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  const actualIdentity = directoryIdentity(userData);
  if (
    metadata.isSymbolicLink() ||
    !metadata.isDirectory() ||
    realpathSync(userData) !== userData ||
    actualIdentity.device !== userDataIdentity.device ||
    actualIdentity.inode !== userDataIdentity.inode
  ) {
    throw new Error("refusing to remove a replaced Electron user-data path");
  }
  rmSync(userData, { force: true, recursive: true });
  try {
    lstatSync(userData);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  throw new Error("Electron dogfood user-data directory was not removed");
}

function removeExactHarnessSnapshot() {
  if (!harnessSnapshotDirectory) return;
  let metadata;
  try {
    metadata = lstatSync(harnessSnapshotDirectory);
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error("immutable harness snapshot directory disappeared");
    }
    throw error;
  }
  const actualIdentity = directoryIdentity(harnessSnapshotDirectory);
  if (
    metadata.isSymbolicLink() ||
    !metadata.isDirectory() ||
    metadata.uid !== process.getuid() ||
    metadata.gid !== process.getgid() ||
    realpathSync(harnessSnapshotDirectory) !== harnessSnapshotDirectory ||
    !harnessSnapshotDirectoryIdentity ||
    actualIdentity.device !== harnessSnapshotDirectoryIdentity.device ||
    actualIdentity.inode !== harnessSnapshotDirectoryIdentity.inode
  ) {
    throw new Error("refusing to remove a replaced harness snapshot path");
  }

  let verificationFailure;
  try {
    if ((metadata.mode & 0o777) !== 0o500) {
      throw new Error("immutable harness snapshot directory mode changed");
    }
    verifyHarnessSnapshotDirectory();
    const expectedEntries = [...harnessSnapshots.values()]
      .map((snapshot) => path.basename(snapshot.path))
      .sort();
    if (
      JSON.stringify(readdirSync(harnessSnapshotDirectory).sort()) !==
      JSON.stringify(expectedEntries)
    ) {
      throw new Error("immutable harness snapshot directory entries changed");
    }
    verifyHarnessSnapshots([...harnessSnapshots.keys()]);
  } catch (error) {
    verificationFailure = error;
  }

  chmodSync(harnessSnapshotDirectory, 0o700);
  const writableIdentity = directoryIdentity(harnessSnapshotDirectory);
  if (
    writableIdentity.device !== harnessSnapshotDirectoryIdentity.device ||
    writableIdentity.inode !== harnessSnapshotDirectoryIdentity.inode
  ) {
    throw new Error("harness snapshot identity changed before cleanup");
  }
  rmSync(harnessSnapshotDirectory, { force: true, recursive: true });
  try {
    lstatSync(harnessSnapshotDirectory);
  } catch (error) {
    if (error?.code === "ENOENT") {
      if (verificationFailure) throw verificationFailure;
      return;
    }
    throw error;
  }
  throw new Error("immutable harness snapshot directory was not removed");
}

async function cleanupApplicationRuntime() {
  if (applicationCleanupPromise) return applicationCleanupPromise;
  applicationCleanupPromise = (async () => {
    const failures = [];

    if (activeHelper) {
      try {
        await ensureHelperStopped(activeHelper);
      } catch (error) {
        failures.push(error);
      }
    }

    if (launchPromise && !application) {
      let timer;
      try {
        await Promise.race([
          launchPromise,
          new Promise((_, reject) => {
            timer = setTimeout(
              () => reject(new Error("Electron launch cleanup timed out")),
              65_000,
            );
          }),
        ]);
      } catch (error) {
        failures.push(error);
      } finally {
        if (timer) clearTimeout(timer);
      }
    }

    if (!electronRootIdentity) discoverUnclaimedElectronProcesses();
    if (electronTrackedProcesses.size > 0) refreshElectronProcesses();
    if (application) {
      try {
        await closeApplicationBounded(application);
      } catch (error) {
        failures.push(error);
      }
    }
    if (electronTrackedProcesses.size > 0) {
      try {
        refreshElectronProcesses();
        const exitedCleanly = await waitForProcessSetExit(
          refreshElectronProcesses,
          500,
        );
        await stopDynamicProcessSet(
          refreshElectronProcesses,
          "Electron process tree",
          3_000,
        );
        if (refreshElectronProcesses().some(sameProcessAlive)) {
          throw new Error("Electron process tree left residue");
        }
        if (!exitedCleanly && !terminationRequested) {
          throw new Error("Electron process tree required forced cleanup");
        }
      } catch (error) {
        failures.push(error);
      }
    }

    try {
      removeExactUserData();
    } catch (error) {
      failures.push(error);
    }
    if (failures.length > 0) {
      throw new AggregateError(failures, "Electron application cleanup failed");
    }
  })();
  return applicationCleanupPromise;
}

async function cleanupEvidenceRuntime() {
  if (evidenceCleanupPromise) return evidenceCleanupPromise;
  evidenceCleanupPromise = (async () => {
    if (watchdog) {
      clearTimeout(watchdog);
      watchdog = null;
    }
    const failures = [];
    if (activeHelper) {
      try {
        await ensureHelperStopped(activeHelper);
      } catch (error) {
        failures.push(error);
      }
    }
    if (protocolMutationGuard) {
      try {
        await pause(25);
        protocolMutationGuard.assertClean();
      } catch (error) {
        failures.push(error);
      } finally {
        protocolMutationGuard.close();
        protocolMutationGuard = null;
      }
    }
    try {
      removeExactHarnessSnapshot();
    } catch (error) {
      failures.push(error);
    }
    if (failures.length > 0) {
      throw new AggregateError(failures, "Electron evidence cleanup failed");
    }
  })();
  return evidenceCleanupPromise;
}

async function cleanupRuntime() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    const failures = [];
    try {
      await cleanupApplicationRuntime();
    } catch (error) {
      failures.push(error);
    }
    try {
      await cleanupEvidenceRuntime();
    } catch (error) {
      failures.push(error);
    }
    if (failures.length > 0) {
      throw new AggregateError(failures, "Electron dogfood cleanup failed");
    }
  })();
  return cleanupPromise;
}

function requestTermination(reason, status) {
  if (terminationPromise) return;
  terminationRequested = true;
  terminationPromise = (async () => {
    let cleanupFailed = false;
    try {
      await cleanupRuntime();
    } catch {
      cleanupFailed = true;
    }
    writeSync(
      2,
      `RESULT:fail reason=${reason} cleanup=${cleanupFailed ? "failed" : "complete"}\n`,
    );
    process.exit(status);
  })();
}

const signalHandlers = {
  SIGINT: () => requestTermination("signal_SIGINT", 130),
  SIGTERM: () => requestTermination("signal_SIGTERM", 143),
};
for (const [signal, handler] of Object.entries(signalHandlers)) {
  process.on(signal, handler);
}

try {
  protocolMutationGuard = startProtocolMutationGuard(protocolRoot, fixture);
  protocolEvidence = verifyProtocolRoot(protocolRoot, fixture);
  protocolMutationGuard.assertClean();

  harnessSnapshotDirectory = mkdtempSync(
    path.join(runtimeRoot, "mozkey-electron-harness."),
  );
  chmodSync(harnessSnapshotDirectory, 0o700);
  harnessSnapshotDirectoryIdentity = directoryIdentity(
    harnessSnapshotDirectory,
  );
  if (
    realpathSync(harnessSnapshotDirectory) !== harnessSnapshotDirectory ||
    statSync(harnessSnapshotDirectory).uid !== process.getuid() ||
    statSync(harnessSnapshotDirectory).gid !== process.getgid() ||
    (statSync(harnessSnapshotDirectory).mode & 0o777) !== 0o700
  ) {
    throw new Error("immutable harness snapshot directory identity is invalid");
  }

  userData = mkdtempSync(path.join(runtimeRoot, "mozkey-electron-dogfood."));
  userDataIdentity = directoryIdentity(userData);
  chmodSync(userData, 0o700);
  userDataIdentity = directoryIdentity(userData);
  snapshotSequenceHelper = rememberHarnessSnapshot(
    "sequenceHelper",
    snapshotTrackedFile(
      trackedSequenceHelper,
      path.join(harnessSnapshotDirectory, path.basename(trackedSequenceHelper)),
      harnessBlobs.helper,
      0o500,
    ),
  );
  rememberHarnessSnapshot(
    "socketVerifier",
    snapshotTrackedFile(
      trackedSocketVerifier,
      path.join(harnessSnapshotDirectory, path.basename(trackedSocketVerifier)),
      harnessBlobs.socketVerifier,
      0o500,
    ),
  );
  snapshotYdotoolRunner = rememberHarnessSnapshot(
    "ydotoolRunner",
    snapshotTrackedFile(
      trackedYdotoolRunner,
      path.join(harnessSnapshotDirectory, path.basename(trackedYdotoolRunner)),
      harnessBlobs.ydotoolRunner,
      0o500,
    ),
  );
  snapshotSurfaceLocator = rememberHarnessSnapshot(
    "surfaceLocator",
    snapshotTrackedFile(
      trackedSurfaceLocator,
      path.join(harnessSnapshotDirectory, path.basename(trackedSurfaceLocator)),
      harnessBlobs.surfaceLocator,
      0o500,
    ),
  );
  snapshotCandidateVerifier = rememberHarnessSnapshot(
    "candidateVerifier",
    snapshotTrackedFile(
      trackedCandidateVerifier,
      path.join(
        harnessSnapshotDirectory,
        path.basename(trackedCandidateVerifier),
      ),
      harnessBlobs.candidateVerifier,
      0o500,
    ),
  );
  snapshotOfficialAttestationVerifier = rememberHarnessSnapshot(
    "officialAttestationVerifier",
    snapshotTrackedFile(
      trackedOfficialAttestationVerifier,
      path.join(
        harnessSnapshotDirectory,
        path.basename(trackedOfficialAttestationVerifier),
      ),
      harnessBlobs.officialAttestationVerifier,
      0o500,
    ),
  );
  rememberHarnessSnapshot(
    "zenzNormalizer",
    snapshotTrackedFile(
      trackedZenzNormalizer,
      path.join(harnessSnapshotDirectory, path.basename(trackedZenzNormalizer)),
      harnessBlobs.zenzNormalizer,
      0o400,
    ),
  );
  const snapshotFixture = rememberHarnessSnapshot(
    "fixture",
    snapshotTrackedFile(
      trackedFixture,
      path.join(harnessSnapshotDirectory, path.basename(trackedFixture)),
      harnessBlobs.fixture,
      0o400,
    ),
  );
  if (
    JSON.stringify(loadReleaseFixture(snapshotFixture.path)) !==
    JSON.stringify(fixture)
  ) {
    throw new Error("fixture changed before its immutable harness snapshot");
  }
  chmodSync(harnessSnapshotDirectory, 0o500);
  harnessSnapshotDirectoryIdentity = directoryIdentity(
    harnessSnapshotDirectory,
  );
  verifyHarnessSnapshots([...harnessSnapshots.keys()]);
  protocolMutationGuard.assertClean();
  const environment = {
    ...baseEnvironment,
    GRIMODEX_USER_DATA_DIR: userData,
    ...(kind === "development"
      ? { GRIMODEX_MCP_PATH: runtimeEvidence.mcpSidecar }
      : {}),
  };
  helperEnvironment = {
    DBUS_SESSION_BUS_ADDRESS: environment.DBUS_SESSION_BUS_ADDRESS,
    HOME: environment.HOME,
    LANG: environment.LANG,
    LC_ALL: environment.LC_ALL,
    LOGNAME: environment.LOGNAME,
    MOZKEY_DOGFOOD_IM: "mozkey-ibg",
    MOZKEY_DOGFOOD_KEY_DELAY_MS: "50",
    MOZKEY_DOGFOOD_SETTLE_DELAY_SECONDS: "1",
    MOZKEY_DOGFOOD_YDOTOOLD_PID: ydotooldPid,
    PATH: "/usr/bin:/bin",
    USER: environment.USER,
    WAYLAND_DISPLAY: environment.WAYLAND_DISPLAY,
    XDG_RUNTIME_DIR: environment.XDG_RUNTIME_DIR,
    XMODIFIERS: environment.XMODIFIERS,
    YDOTOOL_SOCKET: ydotoolSocket,
  };
  watchdog = setTimeout(
    () => requestTermination("whole_probe_timeout", 124),
    300_000,
  );

  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  profileEvidence = await runInstalledCandidateVerifier(true);
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  protocolMutationGuard.assertClean();

  launchPromise = (async () => {
    const candidate = await electron.launch({
      executablePath,
      args: launchArguments,
      cwd: grimodexRepository,
      env: environment,
      timeout: 60_000,
    });
    application = candidate;
    electronRootIdentity = procRecord(candidate.process().pid);
    rememberProcesses(electronTrackedProcesses, [electronRootIdentity]);
    refreshElectronProcesses();
    return candidate;
  })();
  application = await launchPromise;
  const electronProcess = application.process();
  const electronProc = `/proc/${electronProcess.pid}`;
  if (
    statSync(electronProc).uid !== process.getuid() ||
    readlinkSync(`${electronProc}/exe`) !== executablePath ||
    sha256File(`${electronProc}/exe`) !== binarySha256
  ) {
    throw new Error("live Electron process is not the attested executable");
  }
  const electronStartTime = procStartTime(electronProc);
  const window = await application.firstWindow({ timeout: 60_000 });
  await window.waitForLoadState("domcontentloaded");
  if (window.url() !== "app://bundle/index.html") {
    throw new Error(
      "Electron window did not load the attested app renderer URL",
    );
  }
  const applicationState = await application.evaluate(
    ({ app, BrowserWindow }) => {
      const windows = BrowserWindow.getAllWindows().filter(
        (browserWindow) => !browserWindow.isDestroyed(),
      );
      return {
        isPackaged: app.isPackaged,
        defaultApp: process.defaultApp === true,
        executablePath: process.execPath,
        appPath: app.getAppPath(),
        userDataPath: app.getPath("userData"),
        windowCount: windows.length,
      };
    },
  );
  if (applicationState.windowCount !== 1) {
    throw new Error(
      `expected exactly one BrowserWindow, got ${applicationState.windowCount}`,
    );
  }
  if (realpathSync(applicationState.executablePath) !== executablePath) {
    throw new Error("launched Electron executable is not the attested binary");
  }
  if (
    applicationState.userDataPath !== userData ||
    realpathSync(applicationState.userDataPath) !== userData ||
    !sameIdentity(
      userDataIdentity,
      directoryIdentity(applicationState.userDataPath),
    )
  ) {
    throw new Error(
      "Electron app userData path is not the private gate directory",
    );
  }
  if (kind === "packaged" && !applicationState.isPackaged) {
    throw new Error("packaged probe did not report app.isPackaged=true");
  }
  if (
    kind === "development" &&
    (applicationState.isPackaged || !applicationState.defaultApp)
  ) {
    throw new Error(
      "development probe requires app.isPackaged=false and process.defaultApp=true",
    );
  }
  if (
    kind === "packaged" &&
    realpathSync(applicationState.appPath) !== artifactPath
  ) {
    throw new Error("packaged app path is not the attested app artifact");
  }
  if (
    kind === "development" &&
    realpathSync(applicationState.appPath) !== developmentMainTree.directory
  ) {
    throw new Error("development app path is not the attested main tree");
  }
  await application.evaluate(({ BrowserWindow }) => {
    const [candidate] = BrowserWindow.getAllWindows().filter(
      (browserWindow) => !browserWindow.isDestroyed(),
    );
    candidate.show();
    candidate.focus();
  });
  await window.bringToFront();
  await window.evaluate((secureField) => {
    document.title = secureField
      ? "Mozkey Electron Password Probe"
      : "Mozkey Electron Probe";
    const input = document.createElement("input");
    input.id = "mozkey-dogfood-input";
    input.type = secureField ? "password" : "text";
    input.setAttribute("aria-label", "Mozkey dogfood input");
    input.autocomplete = "off";
    input.spellcheck = false;
    input.style.cssText =
      "position:fixed;inset:40px;width:720px;height:72px;font-size:32px;z-index:2147483647";
    document.body.replaceChildren(input);
    input.focus();
  }, secure);
  const input = window.locator("#mozkey-dogfood-input");
  await input.focus();
  await window.waitForFunction(
    () => document.activeElement?.id === "mozkey-dogfood-input",
    undefined,
    { timeout: 10_000 },
  );
  await window.waitForTimeout(250);
  surfaceEvidence = await focusElectronSurface();
  await window.waitForTimeout(250);
  const focusedSurface = await locateElectronSurface(true);
  if (JSON.stringify(focusedSurface) !== JSON.stringify(surfaceEvidence)) {
    throw new Error("AT-SPI Electron surface changed while establishing focus");
  }
  const focusState = await application.evaluate(({ BrowserWindow }) => {
    const windows = BrowserWindow.getAllWindows().filter(
      (browserWindow) => !browserWindow.isDestroyed(),
    );
    return {
      windowCount: windows.length,
      focusedWindowCount: windows.filter((browserWindow) =>
        browserWindow.isFocused(),
      ).length,
    };
  });
  if (focusState.windowCount !== 1 || focusState.focusedWindowCount !== 1) {
    throw new Error(
      `target focus not established: ${JSON.stringify(focusState)}`,
    );
  }
  process.stdout.write(
    `READY:${JSON.stringify({ kind, scopeMode, active: true, focused: true })}\n`,
  );
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  const immediateProfileEvidence = await runInstalledCandidateVerifier(true);
  if (
    JSON.stringify(stableCandidateEvidence(immediateProfileEvidence)) !==
    JSON.stringify(stableCandidateEvidence(profileEvidence))
  ) {
    throw new Error("fresh profile evidence changed before Electron input");
  }
  verifyIbusOwner(fcitxIdentity, ibusIdentity);

  refreshElectronProcesses();
  const sequenceSnapshotNames = [
    "sequenceHelper",
    "socketVerifier",
    "ydotoolRunner",
  ];
  verifyHarnessSnapshots(sequenceSnapshotNames);
  let sequence;
  try {
    sequence = await runSequenceHelper(
      realpathSync(snapshotSequenceHelper.path),
      [reading, secure ? "password" : "direct"],
      helperEnvironment,
    );
  } finally {
    verifyHarnessSnapshots(sequenceSnapshotNames);
  }
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  const helperLines = sequence.stdout.split(/\r?\n/).filter(Boolean);
  if (
    sequence.stderr ||
    helperLines.length !== (secure ? 3 : 4) ||
    helperLines.some(
      (line) => line !== "ydotool socket verified: exact_private_owner",
    )
  ) {
    throw new Error("IME sequence verification transcript was not exact");
  }
  await window.waitForTimeout(1_000);
  const value = await input.inputValue();
  if (!value) {
    throw new Error("IME sequence produced an empty value");
  }
  if (value !== expectedValue) {
    throw new Error(
      "IME value did not exactly match the expected scope result",
    );
  }
  const customApplied = value === expectedCustomValue;
  if (customApplied !== expectedCustomApplied) {
    throw new Error(
      `scope result mismatch: expected customApplied=${expectedCustomApplied}, ` +
        `got ${customApplied}`,
    );
  }
  const finalFocusState = await application.evaluate(({ BrowserWindow }) => {
    const windows = BrowserWindow.getAllWindows().filter(
      (browserWindow) => !browserWindow.isDestroyed(),
    );
    return {
      windowCount: windows.length,
      focusedWindowCount: windows.filter((browserWindow) =>
        browserWindow.isFocused(),
      ).length,
    };
  });
  const activeElement = await window.evaluate(
    () => document.activeElement?.id ?? null,
  );
  if (
    finalFocusState.windowCount !== 1 ||
    finalFocusState.focusedWindowCount !== 1 ||
    activeElement !== "mozkey-dogfood-input"
  ) {
    throw new Error("target focus changed during the IME sequence");
  }
  const finalSurface = await locateElectronSurface(true);
  if (JSON.stringify(finalSurface) !== JSON.stringify(surfaceEvidence)) {
    throw new Error("AT-SPI Electron surface changed during the IME sequence");
  }
  assertFcitxIdentity(
    fcitxIdentity,
    scopeMode,
    unknownScopeValue,
    protocolRoot,
    profileRoot,
  );
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  candidateEvidence = await runInstalledCandidateVerifier();
  assertCandidateMatchesProfilePreflight(profileEvidence, candidateEvidence);
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  const finalProtocolEvidence = verifyProtocolRoot(protocolRoot, fixture);
  if (
    !sameIdentity(
      protocolEvidence.rootIdentity,
      finalProtocolEvidence.rootIdentity,
    ) ||
    protocolEvidence.stateSha256 !== finalProtocolEvidence.stateSha256 ||
    protocolEvidence.projectSha256 !== finalProtocolEvidence.projectSha256
  ) {
    throw new Error("Protocol fixture changed during the gate");
  }
  await pause(25);
  protocolMutationGuard.assertClean();
  if (
    !sameIdentity(binaryIdentity, fileIdentity(executablePath)) ||
    !sameIdentity(artifactIdentity, fileIdentity(artifactPath)) ||
    sha256File(executablePath) !== binarySha256 ||
    sha256File(artifactPath) !== artifactSha256 ||
    statSync(electronProc).uid !== process.getuid() ||
    readlinkSync(`${electronProc}/exe`) !== executablePath ||
    procStartTime(electronProc) !== electronStartTime ||
    sha256File(`${electronProc}/exe`) !== binarySha256
  ) {
    throw new Error("Electron artifact identity changed during the gate");
  }
  if (
    lstatSync(userData).isSymbolicLink() ||
    realpathSync(userData) !== userData ||
    !sameIdentity(userDataIdentity, directoryIdentity(userData))
  ) {
    throw new Error(
      "Electron dogfood user-data identity changed during the gate",
    );
  }
  const finalUserDataPath = await application.evaluate(({ app }) =>
    app.getPath("userData"),
  );
  if (
    finalUserDataPath !== userData ||
    realpathSync(finalUserDataPath) !== userData ||
    !sameIdentity(userDataIdentity, directoryIdentity(finalUserDataPath))
  ) {
    throw new Error("Electron app userData path changed during the gate");
  }
  if (
    runGit(grimodexRepository, ["rev-parse", "HEAD"]) !== expectedHead ||
    runGit(grimodexRepository, [
      "status",
      "--porcelain",
      "--untracked-files=no",
    ]) ||
    runGit(mozkeyRepository, ["rev-parse", "HEAD"]) !== expectedMozkeyHead ||
    runGit(mozkeyRepository, ["status", "--porcelain", "--untracked-files=no"])
  ) {
    throw new Error("release checkout changed during the Electron gate");
  }
  const finalHarnessBlobs = {
    electron: verifyTrackedFile(mozkeyRepository, scriptPath, "100755"),
    fixture: verifyTrackedFile(mozkeyRepository, trackedFixture, "100644"),
    helper: verifyTrackedFile(
      mozkeyRepository,
      trackedSequenceHelper,
      "100755",
    ),
    socketVerifier: verifyTrackedFile(
      mozkeyRepository,
      trackedSocketVerifier,
      "100755",
    ),
    ydotoolRunner: verifyTrackedFile(
      mozkeyRepository,
      trackedYdotoolRunner,
      "100755",
    ),
    surfaceLocator: verifyTrackedFile(
      mozkeyRepository,
      trackedSurfaceLocator,
      "100755",
    ),
    candidateVerifier: verifyTrackedFile(
      mozkeyRepository,
      trackedCandidateVerifier,
      "100755",
    ),
    officialAttestationVerifier: verifyTrackedFile(
      mozkeyRepository,
      trackedOfficialAttestationVerifier,
      "100755",
    ),
    zenzNormalizer: verifyTrackedFile(
      mozkeyRepository,
      trackedZenzNormalizer,
      "100644",
    ),
  };
  if (JSON.stringify(finalHarnessBlobs) !== JSON.stringify(harnessBlobs)) {
    throw new Error("tracked dogfood harness changed during the gate");
  }
  if (
    developmentMainTree &&
    (directoryManifest(developmentMainTree.directory) !==
      developmentMainTree.digest ||
      directoryManifest(developmentRendererTree.directory) !==
        developmentRendererTree.digest)
  ) {
    throw new Error("development Electron build tree changed during the gate");
  }
  verifyElectronRuntimeEvidence();
  verifyHarnessSnapshots([...harnessSnapshots.keys()]);
  if (directoryManifest(playwrightRoot) !== playwrightSha256) {
    throw new Error("Playwright driver tree changed during the gate");
  }
  resultPayload = {
    kind,
    secureField: secure,
    scopeMode,
    scopeEnvironmentVerified: true,
    fcitxGenerationStable: true,
    ibusOwner: ibusIdentity.owner,
    ibusOwnerPid: ibusIdentity.pid,
    ibusOwnerStartTime: ibusIdentity.startTime,
    grimodexHead: expectedHead,
    binarySha256,
    artifactSha256,
    runtimeSha256: runtimeEvidence.digest,
    playwrightVersion: playwrightPackage.version,
    playwrightSha256,
    mozkeyHead: expectedMozkeyHead,
    harnessBlobs,
    protocolStateSha256: protocolEvidence.stateSha256,
    protocolProjectSha256: protocolEvidence.projectSha256,
    candidateAttestationSha256: candidateEvidence.attestationSha256,
    consumerSha256: candidateEvidence.consumerSha256,
    fcitxProfileSha256: candidateEvidence.fcitxProfileSha256,
    profileMarkerSha256: candidateEvidence.profileMarkerSha256,
    profileRootSha256: candidateEvidence.profileRootSha256,
    installedAddonSha256: candidateEvidence.addonSha256,
    installedServerSha256: candidateEvidence.serverSha256,
    serverPid: candidateEvidence.serverPid,
    serverStartTime: candidateEvidence.serverStartTime,
    developmentMainTreeSha256: developmentMainTree?.digest ?? null,
    developmentRendererTreeSha256: developmentRendererTree?.digest ?? null,
    valueChars: Array.from(value).length,
    valueSha256: createHash("sha256").update(value).digest("hex"),
    customApplied,
    expectedCustomApplied,
    isPackaged: applicationState.isPackaged,
    defaultApp: applicationState.defaultApp,
    windowCount: finalFocusState.windowCount,
    focused: finalFocusState.focusedWindowCount === 1,
    activeElementVerified: activeElement === "mozkey-dogfood-input",
    focusMethod: "atspi-screen-extents+verified-ydotool",
    surfacePid: surfaceEvidence.accessiblePid,
    surfaceStartTime: surfaceEvidence.accessibleStartTime,
    surfaceProcDevice: surfaceEvidence.procDevice,
    surfaceProcInode: surfaceEvidence.procInode,
    surfaceOwnerUid: surfaceEvidence.ownerUid,
    surfaceToolkit: surfaceEvidence.toolkitName,
    surfaceRole: surfaceEvidence.role,
    surfaceTitle: surfaceEvidence.title,
    surfaceExtents: {
      x: surfaceEvidence.x,
      y: surfaceEvidence.y,
      width: surfaceEvidence.width,
      height: surfaceEvidence.height,
    },
    surfaceClick: {
      x: surfaceEvidence.clickX,
      y: surfaceEvidence.clickY,
    },
  };
} finally {
  const cleanupFailures = [];
  try {
    await cleanupApplicationRuntime();
  } catch (error) {
    cleanupFailures.push(error);
  }
  if (!resultPayload || cleanupFailures.length > 0 || terminationRequested) {
    try {
      await cleanupEvidenceRuntime();
    } catch (error) {
      cleanupFailures.push(error);
    }
  }
  if (terminationPromise) {
    await terminationPromise;
  }
  if (cleanupFailures.length > 0) {
    throw new AggregateError(
      cleanupFailures,
      "Electron dogfood cleanup failed",
    );
  }
}
if (terminationRequested) await terminationPromise;

let finalVerificationFailure;
let finalEvidenceCleanupFailure;
try {
  protocolMutationGuard.assertClean();
  assertFcitxIdentity(
    fcitxIdentity,
    scopeMode,
    unknownScopeValue,
    protocolRoot,
    profileRoot,
  );
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  const finalCandidateEvidence = await runInstalledCandidateVerifier();
  verifyIbusOwner(fcitxIdentity, ibusIdentity);
  if (
    JSON.stringify(stableCandidateEvidence(finalCandidateEvidence)) !==
    JSON.stringify(stableCandidateEvidence(candidateEvidence))
  ) {
    throw new Error(
      "installed candidate lifetime changed during Electron cleanup",
    );
  }
  resultPayload.consumerSha256 = finalCandidateEvidence.consumerSha256;
  const postCleanupProtocolEvidence = verifyProtocolRoot(protocolRoot, fixture);
  if (
    !sameIdentity(
      protocolEvidence.rootIdentity,
      postCleanupProtocolEvidence.rootIdentity,
    ) ||
    protocolEvidence.stateSha256 !== postCleanupProtocolEvidence.stateSha256 ||
    protocolEvidence.projectSha256 !== postCleanupProtocolEvidence.projectSha256
  ) {
    throw new Error("Protocol fixture changed during Electron cleanup");
  }
  await pause(25);
  protocolMutationGuard.assertClean();
  verifyHarnessSnapshots([...harnessSnapshots.keys()]);
} catch (error) {
  finalVerificationFailure = error;
} finally {
  try {
    await cleanupEvidenceRuntime();
  } catch (error) {
    finalEvidenceCleanupFailure = error;
  }
}
if (finalVerificationFailure || finalEvidenceCleanupFailure) {
  throw new AggregateError(
    [finalVerificationFailure, finalEvidenceCleanupFailure].filter(Boolean),
    "post-cleanup candidate verification failed",
  );
}
if (terminationRequested) await terminationPromise;
for (const [signal, handler] of Object.entries(signalHandlers)) {
  process.off(signal, handler);
}
process.stdout.write(`RESULT:${JSON.stringify(resultPayload)}\n`);
