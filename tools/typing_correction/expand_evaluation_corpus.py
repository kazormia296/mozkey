#!/usr/bin/env python3
"""Expand the authored typing-correction corpus to the release-test size.

The generated TSV files remain checked-in fixtures. This script is kept as a
deterministic data-maintenance tool so the larger synthetic expansion can be
reproduced without hand-editing hundreds of rows.
"""

from __future__ import annotations

import csv
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.typing_correction import validate_typing_correction_corpus as contract


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "src" / "data" / "typing_correction"

QWERTY_NEIGHBORS = {
    "q": "was",
    "w": "qesa",
    "e": "wsdr",
    "r": "edft",
    "t": "rfgy",
    "y": "tghu",
    "u": "yhji",
    "i": "ujko",
    "o": "iklp",
    "p": "ol",
    "a": "qwsz",
    "s": "awedxz",
    "d": "serfcx",
    "f": "drtgvc",
    "g": "ftyhbv",
    "h": "gyujnb",
    "j": "huikmn",
    "k": "jiolm",
    "l": "kop",
    "z": "asx",
    "x": "zsdc",
    "c": "xdfv",
    "v": "cfgb",
    "b": "vghn",
    "n": "bhjm",
    "m": "njk",
}

JIS_KEYS = {
    "1": "ぬ",
    "2": "ふ",
    "3": "あ",
    "4": "う",
    "5": "え",
    "6": "お",
    "7": "や",
    "8": "ゆ",
    "9": "よ",
    "0": "わ",
    "-": "ほ",
    "^": "へ",
    "q": "た",
    "w": "て",
    "e": "い",
    "r": "す",
    "t": "か",
    "y": "ん",
    "u": "な",
    "i": "に",
    "o": "ら",
    "p": "せ",
    "@": "゛",
    "[": "゜",
    "a": "ち",
    "s": "と",
    "d": "し",
    "f": "は",
    "g": "き",
    "h": "く",
    "j": "ま",
    "k": "の",
    "l": "り",
    ";": "れ",
    ":": "け",
    "]": "む",
    "z": "つ",
    "x": "さ",
    "c": "そ",
    "v": "ひ",
    "b": "こ",
    "n": "み",
    "m": "も",
    ",": "ね",
    ".": "る",
    "/": "め",
    "_": "ろ",
}
JIS_ROWS = ("1234567890-^", "qwertyuiop@[", "asdfghjkl;:]", "zxcvbnm,./")

GOLD_FIELDS = contract.GOLD_COLUMNS
NEGATIVE_FIELDS = contract.NEGATIVE_COLUMNS
KANA_GOLD_FIELDS = contract.KANA_GOLD_COLUMNS
KANA_EVALUATION_FIELDS = contract.KANA_EVALUATION_COLUMNS
KANA_NEGATIVE_FIELDS = contract.KANA_NEGATIVE_COLUMNS
ROMAN_HOLDOUT_FIELDS = contract.ROMAN_HOLDOUT_COLUMNS
KANA_HOLDOUT_FIELDS = contract.KANA_HOLDOUT_COLUMNS

TARGET_ROMAN_GOLD = contract.EVALUATION_TARGETS["roman_gold"]
TARGET_ROMAN_NEGATIVE = contract.EVALUATION_TARGETS["roman_negative"]
TARGET_KANA_GOLD = contract.EVALUATION_TARGETS["kana_gold"]
TARGET_KANA_GOLD_FIXTURE = contract.EVALUATION_TARGETS["kana_gold_fixture"]
TARGET_KANA_NEGATIVE = contract.EVALUATION_TARGETS["kana_negative"]
TARGET_ROMAN_HOLDOUT = contract.EVALUATION_TARGETS[
    "roman_composer_engine_holdout"
]
TARGET_KANA_HOLDOUT = contract.EVALUATION_TARGETS["kana_holdout"]
NEGATIVE_AUTO_POLICY = "forbidden"
RAW_POLICY_FORBIDDEN = "forbidden"
RAW_POLICY_ALLOWED = "allowed"
DISPLAY_POLICY_FORBIDDEN = "forbidden"
DISPLAY_POLICY_ALLOWED = "allowed"
STRICT_PRODUCT_NEGATIVE_TARGET = contract.STRICT_NEGATIVE_COVERAGE["roman"][
    "raw_forbidden"
]["lexical"].get("product_name", 0)
DISPLAY_FORBIDDEN_ROMAN_WORDS = frozenset(
    {
        "github",
        "docker",
        "typescript",
        "qwerty",
        "password",
        "protobuf",
        "terminal",
        "compiler",
        "backend",
        "frontend",
        "wayland",
        "cloudflare",
        "chatgpt",
        "electron",
        "javascript",
        "swift",
        "kotlin",
        "rust",
        "golang",
        "android",
        "windows",
        "firefox",
        "gitx",
        "githubactions",
        "workflow",
        "library",
        "dataset",
        "telemetry",
        "sandbox",
        "network",
        "localhost",
        "keyboard",
        "dvorak",
        "imex",
        "jisx",
        "composer",
        "converter",
        "sony",
        "thinkpad",
        "chromebook",
    }
)


def raw_policy_for_roman_reason(reason: str, typed_raw: str) -> str:
    return (
        RAW_POLICY_FORBIDDEN
        if reason in {"url_or_email", "non_letter_input"}
        or not re.fullmatch(r"[a-z]+", typed_raw)
        else RAW_POLICY_ALLOWED
    )


def set_roman_negative_policies(
    row: dict[str, str], *, display_forbidden: bool = False
) -> None:
    row["raw_policy"] = raw_policy_for_roman_reason(row["reason"], row["typed_raw"])
    row["display_policy"] = (
        DISPLAY_POLICY_FORBIDDEN
        if display_forbidden
        else DISPLAY_POLICY_ALLOWED
    )
    row["auto_policy"] = NEGATIVE_AUTO_POLICY


# These readings are deliberately limited to spellings verified by the
# current default Composer table. The C++ replay gate is the final authority.
BASE_WORDS = (
    ("arigatou", "ありがとう", "common", "none"),
    ("yoroshiku", "よろしく", "common", "none"),
    ("kudasai", "ください", "common", "none"),
    ("sumimasen", "すみません", "common", "n"),
    ("otsukaresama", "おつかれさま", "common", "none"),
    ("tabemasu", "たべます", "common", "none"),
    ("nomimasu", "のみます", "common", "none"),
    ("wakarimasu", "わかります", "common", "n"),
    ("dekimasu", "できます", "common", "none"),
    ("ganbarimasu", "がんばります", "common", "n"),
    ("tanoshii", "たのしい", "common", "none"),
    ("kawaii", "かわいい", "common", "none"),
    ("atarashii", "あたらしい", "common", "none"),
    ("omoshiroi", "おもしろい", "common", "none"),
    ("muzukashii", "むずかしい", "common", "none"),
    ("yasashii", "やさしい", "common", "none"),
    ("daijoubu", "だいじょうぶ", "common", "none"),
    ("chotto", "ちょっと", "common", "sokuon"),
    ("massugu", "まっすぐ", "common", "sokuon"),
    ("densha", "でんしゃ", "common", "n"),
    ("gakkou", "がっこう", "common", "sokuon"),
    ("tomodachi", "ともだち", "common", "none"),
    ("toukyou", "とうきょう", "proper_noun", "none"),
    ("oosaka", "おおさか", "proper_noun", "none"),
)

HOLDOUT_BASE_WORDS = (
    ("sora", "そら", "proper_noun", "none"),
    ("mizu", "みず", "common", "none"),
    ("yuki", "ゆき", "proper_noun", "none"),
    ("yama", "やま", "common", "none"),
    ("michi", "みち", "common", "none"),
    ("kuchi", "くち", "common", "none"),
    ("natsu", "なつ", "common", "n"),
    ("kita", "きた", "proper_noun", "none"),
    ("mori", "もり", "proper_noun", "none"),
    ("chotto", "ちょっと", "common", "sokuon"),
    ("arigatou", "ありがとう", "common", "none"),
    ("tabemasu", "たべます", "common", "none"),
    ("tomodachi", "ともだち", "common", "none"),
    ("otsukaresama", "おつかれさま", "common", "none"),
    ("nomimasu", "のみます", "common", "none"),
    ("mise", "みせ", "common", "none"),
)
HOLDOUT_SAFE_ITERATIONS = {
    "sora": (0, 1, 2, 3, 4, 6, 10),
    "mizu": (0, 1, 2, 3, 4, 6, 8, 10),
    "yuki": (0, 1, 2, 3, 4, 6, 8, 10),
    "yama": (0, 1, 2, 3, 4, 5, 6),
    "michi": (0, 1, 2, 3, 5, 6, 10),
    "kuchi": (0, 1, 2, 3, 6, 7, 10),
    "natsu": (0, 1, 2, 3, 5, 6, 13),
    "kita": (0, 1, 2, 3, 4, 5, 6),
    "mori": (0, 1, 3, 4, 6, 8, 10),
    "chotto": (0, 1, 3, 4, 5, 6, 7, 11),
    "arigatou": (0, 3, 8, 9, 17, 18, 50, 53),
    "tabemasu": (0, 1, 2, 5, 7, 9, 10, 11),
    "tomodachi": (0, 1, 4, 5, 6, 10, 16),
    "otsukaresama": (0, 3, 6, 13, 18, 19, 28, 29),
    "nomimasu": (3, 5, 6, 9, 12, 15, 18),
    "mise": (0, 1, 2, 3, 4, 6),
}

TECHNICAL_WORDS = (
    "linux", "python", "typescript", "protobuf", "bazel", "github", "docker",
    "fcitx", "qwerty", "password", "terminal", "compiler", "runtime",
    "backend", "frontend", "unicode", "utf8", "json", "yaml", "toml", "sqlite",
    "vulkan", "wayland", "nvidia", "cloudflare", "openai", "chatgpt",
    "electron", "javascript", "swift", "kotlin", "rust", "golang", "android",
    "windows", "macos", "firefox", "chrome", "git", "githubactions", "workflow",
    "package", "module", "library", "dataset", "telemetry", "sandbox",
    "network", "localhost", "username", "keyboard", "layout", "dvorak",
    "colemak", "ime", "jis", "kana", "romanji", "composer", "engine",
    "converter", "session", "mozkey", "grimodex", "hazkey",
)
PRODUCT_WORDS = (
    "google", "iphone", "ipad", "macbook", "playstation", "nintendo", "pokemon",
    "youtube", "twitter", "discord", "slack", "teams", "notion", "figma",
    "atlassian", "spotify", "netflix", "amazon", "rakuten", "panasonic", "sony",
    "toshiba", "thinkpad", "chromebook", "surface", "xbox", "switch", "kindle",
    "youtubejp", "pixiv", "microsoft", "ubuntu", "debian", "archlinux",
    "androidstudio", "visualstudio", "vscode", "safari", "edge", "zoom", "dropbox",
)
MODIFIER_CASES = (
    ("3", "あ", "3", "ぁ", "small_kana"),
    ("4", "う", "4", "ぅ", "small_kana"),
    ("5", "え", "5", "ぇ", "small_kana"),
    ("6", "お", "6", "ぉ", "small_kana"),
    ("7", "や", "7", "ゃ", "small_kana"),
    ("8", "ゆ", "8", "ゅ", "small_kana"),
    ("9", "よ", "9", "ょ", "small_kana"),
    ("t", "か", "t", "が", "dakuten"),
    ("g", "き", "g", "ぎ", "dakuten"),
    ("h", "く", "h", "ぐ", "dakuten"),
    (":", "け", ":", "げ", "dakuten"),
    ("b", "こ", "b", "ご", "dakuten"),
    ("q", "た", "q", "だ", "dakuten"),
    ("a", "ち", "a", "ぢ", "dakuten"),
    ("z", "つ", "z", "づ", "dakuten"),
    ("w", "て", "w", "で", "dakuten"),
    ("s", "と", "s", "ど", "dakuten"),
    ("x", "さ", "x", "ざ", "dakuten"),
    ("d", "し", "d", "じ", "dakuten"),
    ("r", "す", "r", "ず", "dakuten"),
    ("p", "せ", "p", "ぜ", "dakuten"),
    ("c", "そ", "c", "ぞ", "dakuten"),
    ("f", "は", "f", "ば", "dakuten"),
    ("v", "ひ", "v", "び", "dakuten"),
    ("2", "ふ", "2", "ぶ", "dakuten"),
    ("^", "へ", "^", "べ", "dakuten"),
    ("-", "ほ", "-", "ぼ", "dakuten"),
    ("b", "こ", "b", "ぽ", "handakuten"),
    ("f", "は", "f", "ぱ", "handakuten"),
    ("v", "ひ", "v", "ぴ", "handakuten"),
    ("2", "ふ", "2", "ぷ", "handakuten"),
    ("^", "へ", "^", "ぺ", "handakuten"),
    ("-", "ほ", "-", "ぽ", "handakuten"),
)


def read_tsv(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def write_tsv(path: pathlib.Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows({field: row[field] for field in fields} for row in rows)


def length_bin(raw: str, dataset: str = "roman") -> str:
    observed_length = len(raw)
    buckets = contract.SCHEMA["stratum_dimensions"][dataset]["length"]["buckets"]
    for bucket, bounds in buckets.items():
        if observed_length >= bounds["min"] and (
            "max" not in bounds or observed_length <= bounds["max"]
        ):
            return bucket
    raise ValueError(f"length is outside the {dataset} schema buckets: {raw!r}")


def position_bin(index: int, length: int) -> str:
    if index <= 0:
        return "initial"
    if index >= length - 1:
        return "final"
    return "medial"


def stratum(raw: str, index: int, lexical: str, feature: str) -> str:
    return ";".join(
        (
            f"length={length_bin(raw)}",
            f"position={position_bin(index, len(raw))}",
            f"lexical={lexical}",
            f"feature={feature}",
        )
    )


def holdout_stratum(
    typed: str,
    corrected: str,
    index: int,
    lexical: str,
    feature: str,
    operation: str,
) -> str:
    position = position_bin(index, len(typed))
    # For a duplicate inserted immediately before the final event, the
    # affected pair ends at the final event even though the deletion index is
    # the penultimate typed position.
    if operation == "duplicate" and index >= len(corrected) - 1:
        position = "final"
    return ";".join(
        (
            f"length={length_bin(typed)}",
            f"position={position}",
            f"lexical={lexical}",
            f"feature={feature}",
        )
    )


def infer_feature(raw: str, reading: str = "") -> str:
    if "っ" in reading or any(
        pair in raw for pair in ("kk", "ss", "tt", "pp", "mm", "rr")
    ):
        return "sokuon"
    if "nn" in raw or "ん" in reading:
        return "n"
    if "-" in raw or "ー" in reading:
        return "long_vowel"
    return "none"


def first_error_index(intended: str, typed: str, operation: str) -> int:
    if operation == "duplicate":
        for index in range(len(typed)):
            if typed[:index] + typed[index + 1 :] == intended:
                return index
    if operation == "omission":
        for index in range(len(intended)):
            if intended[:index] + intended[index + 1 :] == typed:
                return index
    for index, (left, right) in enumerate(zip(intended, typed)):
        if left != right:
            return index
    return 0


def normalize_gold(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        row = dict(row)
        previous = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in row.get("stratum", "").split(";")
            if "=" in item
        }
        error_index = first_error_index(
            row["intended_raw"], row["typed_raw"], row["operation"]
        )
        row["stratum"] = stratum(
            row["typed_raw"],
            error_index,
            previous.get("lexical", "common"),
            previous.get(
                "feature", infer_feature(row["intended_raw"], row["intended_reading"])
            ),
        )
        result.append(row)
    return result


def normalize_negative(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        row = dict(row)
        raw_bytes = len(row["typed_raw"].encode("utf-8"))
        if raw_bytes < contract.RUNTIME_LIMITS["min_raw_bytes"]:
            row["typed_raw"] += "x" * (
                contract.RUNTIME_LIMITS["min_raw_bytes"] - raw_bytes
            )
        set_roman_negative_policies(row)
        reason = row["reason"]
        lexical = (
            "product_name"
            if "product" in reason
            else "technical_term"
            if "technical" in reason
            else "common"
        )
        feature = (
            "url"
            if "url" in reason or "email" in reason
            else "intentional"
            if "intentional" in reason
            else "none"
        )
        previous = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in row.get("stratum", "").split(";")
            if "=" in item
        }
        row["stratum"] = (
            f"length={length_bin(row['typed_raw'])};position=none;"
            f"lexical={previous.get('lexical', lexical)};"
            f"feature={previous.get('feature', feature)}"
        )
        result.append(row)
    return result


def make_roman_variant(
    raw: str, operation: str, seed: int, used: set[str]
) -> tuple[str, int]:
    candidates: list[tuple[str, int]] = []
    if operation == "transpose":
        for index in range(len(raw) - 1):
            if raw[index] != raw[index + 1]:
                candidates.append(
                    (
                        raw[:index]
                        + raw[index + 1]
                        + raw[index]
                        + raw[index + 2 :],
                        index,
                    )
                )
    elif operation == "duplicate":
        for index in range(len(raw)):
            candidates.append((raw[:index] + raw[index] + raw[index:], index))
    elif operation == "omission":
        for index in range(len(raw)):
            candidates.append((raw[:index] + raw[index + 1 :], index))
    elif operation == "neighbor":
        for index, character in enumerate(raw):
            for neighbor in QWERTY_NEIGHBORS.get(character, ""):
                candidates.append(
                    (raw[:index] + neighbor + raw[index + 1 :], index)
                )
    elif operation == "replacement":
        alphabet = "qwertyuiopasdfghjklzxcvbnm"
        for index, character in enumerate(raw):
            for replacement in alphabet:
                if (
                    replacement != character
                    and replacement not in QWERTY_NEIGHBORS.get(character, "")
                ):
                    candidates.append(
                        (raw[:index] + replacement + raw[index + 1 :], index)
                    )
    for offset in range(len(candidates)):
        candidate, index = candidates[(seed + offset) % len(candidates)]
        if len(candidate) >= 2 and candidate not in used:
            return candidate, index
    raise RuntimeError(f"could not find unique {operation} variant for {raw}")


def expand_roman_gold(
    rows: list[dict[str, str]], negative_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    rows = normalize_gold(rows)
    used = {row["typed_raw"] for row in rows} | {
        row["typed_raw"] for row in negative_rows
    }
    operations = ("transpose", "duplicate", "omission", "neighbor", "replacement")
    for round_index in range(4):
        for base_index, (raw, reading, lexical, feature) in enumerate(BASE_WORDS):
            for operation_index, operation in enumerate(operations):
                if len(rows) >= TARGET_ROMAN_GOLD:
                    return rows
                typed, error_index = make_roman_variant(
                    raw,
                    operation,
                    round_index * len(BASE_WORDS) + base_index + operation_index,
                    used,
                )
                used.add(typed)
                auto = operation in {"transpose", "duplicate"} and (
                    base_index + round_index
                ) % 5 == 0
                rows.append(
                    {
                        "case_id": f"R{len(rows) + 1:06d}",
                        "intended_raw": raw,
                        "typed_raw": typed,
                        "intended_reading": reading,
                        "operation": operation,
                        "confidence": "high" if auto else "medium",
                        "behavior": "auto" if auto else "suggest",
                        "stratum": stratum(typed, error_index, lexical, feature),
                        "note": f"generated stratified {operation} fixture",
                    }
                )
    if len(rows) != TARGET_ROMAN_GOLD:
        raise RuntimeError(f"Roman Gold expansion produced {len(rows)} rows")
    return rows


def negative_candidates() -> list[tuple[str, str, str]]:
    normal = (
        "asagohan hirugohan bangohan mainichi itsumo tokidoki hontou chigau omou "
        "kangaeru tsukau tsukuru hajimaru owarimasu kaeru modoru noru oriru aruku "
        "hashiru oyogu asobu hataraku yasumu benkyou renshuu shukudai shigoto "
        "kaimono ryouri omiyage omocha jikan ashita kinou sayonara tadaima "
        "okaerinasai ittekimasu oyasuminasai atsukatta atatakai samui atsui kirei "
        "genki benri anzen daiji byouin kaisha sensei gakusei kazoku otousan "
        "okaasan imouto oniisan oniichan toukyou oosaka nagoya kyouto pokemon "
        "panasonikku soni- denwa tegami shashin ongaku eiga honya kippu jisho "
        "kouen mizu kuruma jitensha basu hikouki tabi ryokou kaigi yakusoku "
        "mondai kotae seikatsu shizen sekai nihon nihongo eigo kotoba namae denki "
        "yotei"
    ).split()
    intentional = (
        "aaaa zzzz ssss asdf qwer zxcv hjkl mnbv qqqq wwww eeee rrrr tttt yyyy "
        "uuuu iiii oooo pppp ffff gggg hhhh jjjj kkkk llll mmmm nnnn bbbb cccc "
        "vvvv xxxx yyyyy zzzzz abab baba kaka sasa tata nana rara mama wawa yaya "
        "papa gaga hihi jaja lulu xaxa"
    ).split()
    url_like = []
    for index in range(80):
        if index % 4 == 0:
            url_like.append(f"https://example{index}.com/path")
        elif index % 4 == 1:
            url_like.append(f"http://localhost:{8000 + index}")
        elif index % 4 == 2:
            url_like.append(f"user{index}@example.com")
        else:
            url_like.append(f"/home/user{index}/project")
    non_letter = tuple(
        (f"case{index:02d}", "case", "non_letter_input") for index in range(50)
    )
    return (
        [(value, "normal", "normal") for value in normal]
        + [(value, "technical", "technical_term") for value in TECHNICAL_WORDS]
        + [(value, "product", "product_name") for value in PRODUCT_WORDS]
        + [(value, "url", "url_or_email") for value in url_like]
        + list(non_letter)
        + [
            (value, "intentional", "incomplete_or_intentional")
            for value in intentional
        ]
    )


def expand_roman_negative(
    rows: list[dict[str, str]], gold_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    rows = normalize_negative(rows)
    strict_product_count = sum(
        row["raw_policy"] == RAW_POLICY_FORBIDDEN
        and dict(item.split("=", 1) for item in row["stratum"].split(";"))["lexical"]
        == "product_name"
        for row in rows
    )
    existing_inputs = {row["typed_raw"] for row in rows}
    for row in rows:
        fields = dict(item.split("=", 1) for item in row["stratum"].split(";"))
        if (
            fields["lexical"] != "product_name"
            or row["raw_policy"] == RAW_POLICY_FORBIDDEN
            or strict_product_count >= STRICT_PRODUCT_NEGATIVE_TARGET
        ):
            continue
        typed_raw = row["typed_raw"] + "0"
        if typed_raw in existing_inputs:
            continue
        existing_inputs.add(typed_raw)
        row["typed_raw"] = typed_raw
        row["raw_policy"] = RAW_POLICY_FORBIDDEN
        row["display_policy"] = DISPLAY_POLICY_ALLOWED
        fields["length"] = length_bin(typed_raw)
        row["stratum"] = ";".join(
            f"{key}={fields[key]}" for key in contract.STRATUM_FIELDS
        )
        strict_product_count += 1
    strict_target = contract.COVERAGE_MINIMUMS["roman_negative"]["raw_policy"][
        "forbidden"
    ]
    strict_count = sum(
        row["raw_policy"] == RAW_POLICY_FORBIDDEN for row in rows
    )
    for row in rows:
        if strict_count <= strict_target:
            break
        if (
            row["raw_policy"] != RAW_POLICY_FORBIDDEN
            or row["reason"] != "normal"
            or not row["typed_raw"].endswith("0")
        ):
            continue
        candidate_raw = row["typed_raw"][:-1]
        if candidate_raw in existing_inputs:
            continue
        existing_inputs.remove(row["typed_raw"])
        existing_inputs.add(candidate_raw)
        row["typed_raw"] = candidate_raw
        row["raw_policy"] = RAW_POLICY_ALLOWED
        fields = dict(item.split("=", 1) for item in row["stratum"].split(";"))
        fields["length"] = length_bin(candidate_raw)
        row["stratum"] = ";".join(
            f"{key}={fields[key]}" for key in contract.STRATUM_FIELDS
        )
        strict_count -= 1
    strict_count = sum(
        row["raw_policy"] == RAW_POLICY_FORBIDDEN for row in rows
    )
    for row in rows:
        if strict_count >= strict_target:
            break
        if row["raw_policy"] == RAW_POLICY_FORBIDDEN or len(row["typed_raw"]) > 5:
            continue
        row["typed_raw"] += "0"
        row["raw_policy"] = RAW_POLICY_FORBIDDEN
        row["display_policy"] = DISPLAY_POLICY_ALLOWED
        fields = dict(item.split("=", 1) for item in row["stratum"].split(";"))
        fields["length"] = length_bin(row["typed_raw"])
        row["stratum"] = ";".join(
            f"{key}={fields[key]}" for key in contract.STRATUM_FIELDS
        )
        strict_count += 1
    if strict_count < strict_target:
        raise RuntimeError(
            f"Roman Negative expansion produced only {strict_count} strict rows"
        )
    used = {row["typed_raw"] for row in rows} | {
        row["typed_raw"] for row in gold_rows
    }
    for raw, expected, reason in negative_candidates():
        if len(rows) >= TARGET_ROMAN_NEGATIVE:
            break
        typed_raw = raw
        raw_policy = raw_policy_for_roman_reason(reason, typed_raw)
        if (
            reason == "product_name"
            and strict_product_count < STRICT_PRODUCT_NEGATIVE_TARGET
        ):
            typed_raw += "0"
            raw_policy = RAW_POLICY_FORBIDDEN
        if typed_raw in used or not re.fullmatch(r"[a-z0-9@:/._+?=-]+", typed_raw):
            continue
        used.add(typed_raw)
        lexical = (
            "product_name"
            if reason == "product_name"
            else "technical_term"
            if reason == "technical_term"
            else "common"
        )
        feature = "url" if reason == "url_or_email" else "intentional" if "intentional" in reason else "none"
        rows.append(
            {
                "case_id": f"N{len(rows) + 1:06d}",
                "typed_raw": typed_raw,
                "expected_reading": expected,
                "reason": reason,
                "raw_policy": raw_policy,
                "display_policy": DISPLAY_POLICY_ALLOWED,
                "auto_policy": NEGATIVE_AUTO_POLICY,
                "stratum": f"length={length_bin(typed_raw)};position=none;"
                f"lexical={lexical};feature={feature}",
            }
        )
        if reason == "product_name" and raw_policy == RAW_POLICY_FORBIDDEN:
            strict_product_count += 1
    if len(rows) != TARGET_ROMAN_NEGATIVE:
        raise RuntimeError(f"Roman Negative expansion produced {len(rows)} rows")
    display_target = contract.NEGATIVE_POLICY_TARGETS["roman"]["display_forbidden"]
    display_candidates = [
        row
        for row in rows
        if row["raw_policy"] == RAW_POLICY_ALLOWED
        and row["typed_raw"] in DISPLAY_FORBIDDEN_ROMAN_WORDS
    ]
    if len(display_candidates) != display_target:
        raise RuntimeError(
            "Roman display-forbidden fixture words must be present exactly once"
        )
    for row in display_candidates[:display_target]:
        row["display_policy"] = DISPLAY_POLICY_FORBIDDEN
    if sum(row["display_policy"] == DISPLAY_POLICY_FORBIDDEN for row in rows) != display_target:
        raise RuntimeError(
            f"Roman Negative expansion produced fewer than {display_target} display-forbidden rows"
        )
    return rows


def neighbor_pairs() -> list[tuple[str, str]]:
    result = []
    for row in JIS_ROWS:
        for left, right in zip(row, row[1:]):
            result.append((left, right))
            result.append((right, left))
    return result


def normalize_kana_gold(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    modifier_features = {
        (code, typed, corrected_code, corrected): feature
        for code, typed, corrected_code, corrected, feature in MODIFIER_CASES
    }
    result = []
    for row in rows:
        row = dict(row)
        feature = modifier_features.get(
            (
                row.get("typed_key_codes", ""),
                row.get("typed_key_strings", ""),
                row.get("corrected_key_codes", ""),
                row.get("corrected_key_strings", ""),
            ),
            "kana_modifier",
        )
        row.setdefault(
            "stratum",
            "length=short;position=single;lexical=common;feature="
            + (feature if row["operation"] == "kana_modifier" else "kana_neighbor"),
        )
        if row["operation"] == "kana_modifier":
            fields = dict(item.split("=", 1) for item in row["stratum"].split(";"))
            fields["feature"] = feature
            row["stratum"] = ";".join(f"{key}={fields[key]}" for key in contract.STRATUM_FIELDS)
        # Kana automatic correction is intentionally disabled.  The fixture
        # still records the physical rule, but every release-facing result is
        # suggestion-only until a separate auto policy is approved.
        row["behavior"] = "suggest"
        result.append(row)
    return result


def expand_kana_gold(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    rows = normalize_kana_gold(rows)
    used = {
        (
            row["typed_key_codes"],
            row["typed_key_strings"],
            row["corrected_key_codes"],
            row["corrected_key_strings"],
        )
        for row in rows
    }
    for code, typed, corrected_code, corrected, feature in MODIFIER_CASES:
        if len(rows) >= TARGET_KANA_GOLD:
            break
        pair = (code, typed, corrected_code, corrected)
        if pair in used:
            continue
        used.add(pair)
        rows.append(
            {
                "case_id": f"K{len(rows) + 1:06d}",
                "typed_key_codes": code,
                "typed_key_strings": typed,
                "corrected_key_codes": corrected_code,
                "corrected_key_strings": corrected,
                "operation": "kana_modifier",
                "confidence": "medium",
                "behavior": "suggest",
                "stratum": f"length=short;position=single;lexical=common;feature={feature}",
                "note": "generated modifier-sensitive fixture",
            }
        )
    for left, right in neighbor_pairs():
        if len(rows) >= TARGET_KANA_GOLD:
            break
        pair = (left, JIS_KEYS[left], right, JIS_KEYS[right])
        if pair in used:
            continue
        used.add(pair)
        rows.append(
            {
                "case_id": f"K{len(rows) + 1:06d}",
                "typed_key_codes": left,
                "typed_key_strings": JIS_KEYS[left],
                "corrected_key_codes": right,
                "corrected_key_strings": JIS_KEYS[right],
                "operation": "neighbor",
                "confidence": "medium",
                "behavior": "suggest",
                "stratum": "length=short;position=single;lexical=common;feature=kana_neighbor",
                "note": "generated JIS horizontal-neighbor fixture",
            }
        )
    if len(rows) != TARGET_KANA_GOLD:
        raise RuntimeError(f"Kana Gold expansion produced {len(rows)} rows")
    return rows


def kana_stratum(length: int, position: str, feature: str) -> str:
    return (
        f"length={length_bin('x' * length, 'kana')};position={position};"
        f"lexical=common;feature={feature}"
    )


def expand_kana_evaluation(
    fixture_rows: list[dict[str, str]],
    protected_inputs: set[tuple[str, str]],
) -> list[dict[str, str]]:
    """Create unique multi-key evaluation traces from one-event rule fixtures."""
    key_pool = tuple(key for key in JIS_KEYS if key not in "5@[")
    used_inputs = set(protected_inputs)
    result: list[dict[str, str]] = []
    for index in range(TARGET_KANA_GOLD):
        source = fixture_rows[index % len(fixture_rows)]
        length = 3 if index < 80 else 4
        position_kind = ("initial", "medial", "final")[index % 3]
        feature = dict(
            item.split("=", 1) for item in source["stratum"].split(";")
        )["feature"]
        for attempt in range(len(key_pool) * 4):
            filler = [
                key_pool[(index * 7 + attempt * 3 + offset) % len(key_pool)]
                for offset in range(length - 1)
            ]
            wrong = source["typed_key_codes"]
            corrected = source["corrected_key_codes"]
            wrong_string = source["typed_key_strings"]
            corrected_string = source["corrected_key_strings"]
            filler_strings = make_kana_trace("".join(filler))
            if position_kind == "initial":
                typed_codes = wrong + "".join(filler)
                corrected_codes = corrected + "".join(filler)
                typed_strings = wrong_string + filler_strings
                corrected_strings = corrected_string + filler_strings
            elif position_kind == "medial":
                split = length // 2
                typed_codes = "".join(filler[: split - 1]) + wrong + "".join(
                    filler[split - 1 :]
                )
                corrected_codes = "".join(filler[: split - 1]) + corrected + "".join(
                    filler[split - 1 :]
                )
                prefix_strings = make_kana_trace("".join(filler[: split - 1]))
                suffix_strings = make_kana_trace("".join(filler[split - 1 :]))
                typed_strings = prefix_strings + wrong_string + suffix_strings
                corrected_strings = (
                    prefix_strings + corrected_string + suffix_strings
                )
            else:
                typed_codes = "".join(filler) + wrong
                corrected_codes = "".join(filler) + corrected
                typed_strings = filler_strings + wrong_string
                corrected_strings = filler_strings + corrected_string
            typed_input = (typed_codes, typed_strings)
            if typed_input in used_inputs:
                continue
            used_inputs.add(typed_input)
            result.append(
                {
                    "case_id": f"KE{index + 1:06d}",
                    "typed_key_codes": typed_codes,
                    "typed_key_strings": typed_strings,
                    "corrected_key_codes": corrected_codes,
                    "corrected_key_strings": corrected_strings,
                    "operation": source["operation"],
                    "confidence": source["confidence"],
                    "behavior": "suggest",
                    "stratum": kana_stratum(length, position_kind, feature),
                    "note": "unique multi-key evaluation trace derived from rule fixture",
                }
            )
            break
        else:
            raise RuntimeError(f"could not create unique Kana evaluation case {index}")
    return result


def normalize_kana_negative(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        row = dict(row)
        previous = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in row.get("stratum", "").split(";")
            if "=" in item
        }
        row["stratum"] = (
            f"length={length_bin(row['typed_key_codes'], 'kana')};position=none;"
            f"lexical={previous.get('lexical', 'common')};"
            f"feature={previous.get('feature', 'multi_key')}"
        )
        fields = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in row["stratum"].split(";")
        }
        row["raw_policy"] = (
            RAW_POLICY_FORBIDDEN
            if fields["feature"] == "unknown_key"
            else RAW_POLICY_ALLOWED
        )
        row["display_policy"] = DISPLAY_POLICY_ALLOWED
        row["auto_policy"] = NEGATIVE_AUTO_POLICY
        result.append(row)
    return result


def expand_kana_negative(
    rows: list[dict[str, str]],
    protected_inputs: set[tuple[str, str]],
) -> list[dict[str, str]]:
    normalized = normalize_kana_negative(rows)
    # Keep a balanced gate-eligible display-forbidden sample from the authored
    # negatives; unknown-key rows are regenerated as gate-invalid traces so
    # raw-policy safety and display-policy safety have separate denominators.
    suggestion_rows = [
        row
        for row in normalized
        if dict(item.split("=", 1) for item in row["stratum"].split(";"))["feature"]
        != "unknown_key"
    ]
    suggestion_rows = suggestion_rows[: TARGET_KANA_NEGATIVE // 2]
    result = [dict(row) for row in suggestion_rows]
    for row in result:
        row["raw_policy"] = RAW_POLICY_ALLOWED
        row["display_policy"] = DISPLAY_POLICY_FORBIDDEN
    used = set(protected_inputs) | {
        (row["typed_key_codes"], row["typed_key_strings"]) for row in result
    }
    unknown_keys = "!#$%&*()=~"
    strict_target = contract.COVERAGE_MINIMUMS["kana_negative"]["raw_policy"][
        "forbidden"
    ]
    strict_index = 0
    strict_medium_target = contract.STRICT_NEGATIVE_COVERAGE["kana"][
        "raw_forbidden"
    ]["length"]["medium"]
    strict_medium_count = 0
    for left in unknown_keys:
        for right in unknown_keys:
            right_index = unknown_keys.index(right)
            suffixes = (
                (
                    unknown_keys[(right_index + 1) % len(unknown_keys)]
                    + unknown_keys[(right_index + 2) % len(unknown_keys)],
                )
                if strict_medium_count < strict_medium_target
                else ()
            ) + ("", unknown_keys[(right_index + 1) % len(unknown_keys)])
            for suffix in suffixes:
                if strict_index >= strict_target:
                    break
                code = left + right + suffix
                if any(code[index] == code[index + 1] for index in range(len(code) - 1)):
                    continue
                if code in {item[0] for item in used}:
                    continue
                key_strings = "?" * len(code)
                if (code, key_strings) in used:
                    continue
                used.add((code, key_strings))
                result.append(
                    {
                        "case_id": f"KN{len(result) + 1:06d}",
                        "typed_key_codes": code,
                        "typed_key_strings": key_strings,
                        "raw_policy": RAW_POLICY_FORBIDDEN,
                        "display_policy": DISPLAY_POLICY_ALLOWED,
                        "auto_policy": NEGATIVE_AUTO_POLICY,
                        "stratum": f"length={length_bin(code, 'kana')};position=none;"
                        "lexical=common;feature=unknown_key",
                        "reason": "unknown_key",
                    }
                )
                strict_index += 1
                if len(code) == 4:
                    strict_medium_count += 1
            if strict_index >= strict_target:
                break
        if strict_index >= strict_target:
            break
    if strict_index != strict_target:
        raise RuntimeError(f"could not create {strict_target} strict Kana negatives")
    for index, row in enumerate(result, start=1):
        row["case_id"] = f"KN{index:06d}"
    if len(result) != TARGET_KANA_NEGATIVE:
        raise RuntimeError(f"Kana Negative expansion produced {len(result)} rows")
    return result


def expand_roman_holdout(used: set[str]) -> list[dict[str, str]]:
    result = []
    operations = ("transpose", "duplicate", "neighbor")
    for raw, reading, lexical, feature in HOLDOUT_BASE_WORDS:
        local_used: set[str] = set()
        for iteration in range(max(HOLDOUT_SAFE_ITERATIONS[raw]) + 1):
            operation = operations[iteration % len(operations)]
            try:
                typed, error_index = make_roman_variant(
                    raw, operation, iteration + 2, local_used
                )
            except RuntimeError:
                if iteration in HOLDOUT_SAFE_ITERATIONS[raw]:
                    raise
                continue
            local_used.add(typed)
            if iteration not in HOLDOUT_SAFE_ITERATIONS[raw]:
                continue
            if len(result) >= TARGET_ROMAN_HOLDOUT:
                return result
            if typed in used:
                continue
            used.add(typed)
            result.append(
                {
                    "case_id": f"H{len(result) + 1:06d}",
                    "typed_raw": typed,
                    "corrected_raw": raw,
                    "corrected_reading": reading,
                    "operation": operation,
                    "behavior": "test_only",
                    "stratum": holdout_stratum(
                        typed, raw, error_index, lexical, feature, operation
                    ),
                    "note": "independent Japanese Composer/Engine holdout",
                }
            )
    if len(result) != TARGET_ROMAN_HOLDOUT:
        raise RuntimeError(f"Roman holdout expansion produced {len(result)} rows")
    return result


def make_kana_trace(codes: str) -> str:
    return "".join(JIS_KEYS.get(code, "?") for code in codes)


KANA_LONG_MODIFIER_INDICES = frozenset({0, 4, 8, 12, 16, 20, 24, 28})


def kana_holdout_suffix(
    index: int,
    default: str,
    *,
    allow_long_modifier: bool = False,
    medium_period: int = 10,
) -> str:
    if allow_long_modifier:
        if index in KANA_LONG_MODIFIER_INDICES:
            return "qazwsedrf"
        if index == 10:
            return "qaz"
        return default
    if index % medium_period == 0:
        return "qaz"
    return default


def contains_auto_kana_event(codes: str, key_strings: str) -> bool:
    return any(
        code == "5" and key_string == "え"
        for code, key_string in zip(codes, key_strings)
    )


def expand_kana_holdout(
    generated_inputs: set[tuple[str, str]]
) -> list[dict[str, str]]:
    result = []
    pairs = neighbor_pairs()
    used_inputs = set(generated_inputs)
    used: set[tuple[str, str, str, str]] = set()
    for index, (left, right) in enumerate(pairs):
        if len(result) >= 60:
            break
        if left in "@[":
            continue
        suffix = kana_holdout_suffix(index, JIS_ROWS[index % len(JIS_ROWS)][0])
        final_position = index % 16 == 0
        typed_codes = suffix + left if final_position else left + suffix
        corrected_codes = suffix + right if final_position else right + suffix
        pair = (
            typed_codes,
            make_kana_trace(typed_codes),
            corrected_codes,
            make_kana_trace(corrected_codes),
        )
        if (
            contains_auto_kana_event(typed_codes, pair[1])
            or pair in used
            or (typed_codes, make_kana_trace(typed_codes)) in used_inputs
        ):
            continue
        used.add(pair)
        used_inputs.add((typed_codes, make_kana_trace(typed_codes)))
        result.append(
            {
                "case_id": f"KH{len(result) + 1:06d}",
                "typed_key_codes": typed_codes,
                "typed_key_strings": make_kana_trace(typed_codes),
                "corrected_key_codes": corrected_codes,
                "corrected_key_strings": make_kana_trace(corrected_codes),
                "operation": "neighbor",
                "behavior": "test_only",
                "stratum": f"length={length_bin(typed_codes, 'kana')};position="
                f"{'final' if final_position else 'initial'};"
                "lexical=common;feature=kana_neighbor",
                "note": "independent multi-key JIS neighbor holdout",
            }
        )
    for index, (code, typed, corrected_code, corrected, feature) in enumerate(
        MODIFIER_CASES
    ):
        if len(result) >= 60 + len(MODIFIER_CASES):
            break
        suffix = kana_holdout_suffix(
            index,
            JIS_ROWS[index % len(JIS_ROWS)][(index + 1) % len(JIS_ROWS[0])],
            allow_long_modifier=True,
        )
        final_position = index % 6 == 0
        typed_codes = suffix + code if final_position else code + suffix
        corrected_codes = (
            suffix + corrected_code if final_position else corrected_code + suffix
        )
        suffix_strings = make_kana_trace(suffix)
        typed_strings = suffix_strings + typed if final_position else typed + suffix_strings
        corrected_strings = (
            suffix_strings + corrected
            if final_position
            else corrected + suffix_strings
        )
        pair = (
            typed_codes,
            typed_strings,
            corrected_codes,
            corrected_strings,
        )
        if (
            contains_auto_kana_event(typed_codes, pair[1])
            or pair in used
            or (typed_codes, pair[1]) in used_inputs
        ):
            continue
        used.add(pair)
        used_inputs.add((typed_codes, typed_strings))
        result.append(
            {
                "case_id": f"KH{len(result) + 1:06d}",
                "typed_key_codes": typed_codes,
                "typed_key_strings": typed_strings,
                "corrected_key_codes": corrected_codes,
                "corrected_key_strings": corrected_strings,
                "operation": "kana_modifier",
                "behavior": "test_only",
                "stratum": f"length={length_bin(typed_codes, 'kana')};position="
                f"{'final' if final_position else 'initial'};"
                f"lexical=common;feature={feature}",
                "note": "independent modifier-sensitive JIS holdout",
            }
        )
    keys = tuple(key for key in JIS_KEYS if key not in "@[")
    for key in keys:
        for suffix in keys:
            if len(result) >= TARGET_KANA_HOLDOUT:
                break
            suffix = kana_holdout_suffix(len(result), suffix, medium_period=20)
            final_position = len(result) % 12 == 0
            typed_codes = suffix + key + key if final_position else key + key + suffix
            corrected_codes = suffix + key if final_position else key + suffix
            pair = (
                typed_codes,
                make_kana_trace(typed_codes),
                corrected_codes,
                make_kana_trace(corrected_codes),
            )
            if (
                contains_auto_kana_event(typed_codes, pair[1])
                or pair in used
                or (typed_codes, pair[1]) in used_inputs
            ):
                continue
            used.add(pair)
            used_inputs.add((typed_codes, make_kana_trace(typed_codes)))
            result.append(
                {
                    "case_id": f"KH{len(result) + 1:06d}",
                    "typed_key_codes": typed_codes,
                    "typed_key_strings": make_kana_trace(typed_codes),
                    "corrected_key_codes": corrected_codes,
                    "corrected_key_strings": make_kana_trace(corrected_codes),
                    "operation": "duplicate",
                    "behavior": "test_only",
                    "stratum": f"length={length_bin(typed_codes, 'kana')};position="
                    f"{'final' if final_position else 'medial'};"
                    "lexical=common;feature=duplicate",
                    "note": "independent multi-key duplicate holdout",
                }
            )
        if len(result) >= TARGET_KANA_HOLDOUT:
            break
    if len(result) != TARGET_KANA_HOLDOUT:
        raise RuntimeError(f"Kana holdout expansion produced {len(result)} rows")
    return result


def main() -> int:
    gold_path = CORPUS_ROOT / "roman_gold.tsv"
    negative_path = CORPUS_ROOT / "roman_negative.tsv"
    original_negative = read_tsv(negative_path)
    roman_gold = expand_roman_gold(read_tsv(gold_path), original_negative)
    roman_negative = expand_roman_negative(original_negative, roman_gold)
    write_tsv(gold_path, GOLD_FIELDS, roman_gold)
    write_tsv(negative_path, NEGATIVE_FIELDS, roman_negative)

    kana_gold_path = CORPUS_ROOT / "kana_gold.tsv"
    kana_evaluation_path = CORPUS_ROOT / "kana_evaluation.tsv"
    kana_negative_path = CORPUS_ROOT / "kana_negative.tsv"
    kana_gold = expand_kana_gold(read_tsv(kana_gold_path))
    kana_evaluation = expand_kana_evaluation(
        kana_gold,
        {
            (row["typed_key_codes"], row["typed_key_strings"])
            for row in read_tsv(kana_negative_path)
        },
    )
    kana_negative = expand_kana_negative(
        read_tsv(kana_negative_path),
        {
            (row["typed_key_codes"], row["typed_key_strings"])
            for row in kana_gold + kana_evaluation
        },
    )
    write_tsv(kana_gold_path, KANA_GOLD_FIELDS, kana_gold)
    write_tsv(kana_evaluation_path, KANA_EVALUATION_FIELDS, kana_evaluation)
    write_tsv(kana_negative_path, KANA_NEGATIVE_FIELDS, kana_negative)

    modifier_path = CORPUS_ROOT / "kana_modifier_rules.tsv"
    modifier_rows = read_tsv(modifier_path)
    for row in modifier_rows:
        if row["operation"] == "kana_modifier":
            row["cost"] = "100"
    modifier_ids = {row["rule_id"] for row in modifier_rows}
    for row in kana_gold:
        if row["operation"] != "kana_modifier" or row["case_id"] in modifier_ids:
            continue
        modifier_rows.append(
            {
                "rule_id": row["case_id"],
                "wrong_key_code": row["typed_key_codes"],
                "wrong_key_string": row["typed_key_strings"],
                "corrected_key_code": row["corrected_key_codes"],
                "corrected_key_string": row["corrected_key_strings"],
                "operation": "kana_modifier",
                "cost": "100",
                "behavior": "suggest",
                "note": "generated modifier-sensitive fixture",
            }
        )
    write_tsv(
        modifier_path,
        (
            "rule_id",
            "wrong_key_code",
            "wrong_key_string",
            "corrected_key_code",
            "corrected_key_string",
            "operation",
            "cost",
            "behavior",
            "note",
        ),
        modifier_rows,
    )

    write_tsv(
        CORPUS_ROOT / "roman_holdout.tsv",
        ROMAN_HOLDOUT_FIELDS,
        expand_roman_holdout(
            {row["typed_raw"] for row in roman_gold + roman_negative}
        ),
    )
    write_tsv(
        CORPUS_ROOT / "kana_holdout.tsv",
        KANA_HOLDOUT_FIELDS,
        expand_kana_holdout(
            {
                (row["typed_key_codes"], row["typed_key_strings"])
                for row in kana_gold + kana_evaluation + kana_negative
            }
        ),
    )
    print(f"expanded Roman Gold/Negative to {TARGET_ROMAN_GOLD}/{TARGET_ROMAN_NEGATIVE}")
    print(
        f"expanded Kana fixture/evaluation/Negative to "
        f"{TARGET_KANA_GOLD_FIXTURE}/{TARGET_KANA_GOLD}/{TARGET_KANA_NEGATIVE}"
    )
    print(
        f"expanded Roman/Kana holdout to "
        f"{TARGET_ROMAN_HOLDOUT}/{TARGET_KANA_HOLDOUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
