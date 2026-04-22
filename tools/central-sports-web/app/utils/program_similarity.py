"""プログラム名の類似度計算。

alias 自動学習の「表記揺れ」と「プログラム差し替え」を見分けるための
フェイルセーフ。以下 5 指標の中央値で判断する。

- Levenshtein 比 (`fuzz.ratio` / 100.0)
  全体の編集距離ベース。文字列長が近いほど敏感。
- Jaro-Winkler 類似度
  先頭一致を重視。短い前置語や prefix の違い/一致に強い。
- 文字 3-gram Jaccard
  語順入れ替えや途中挿入に強い。rapidfuzz に無いので自作。
- partial_ratio (`fuzz.partial_ratio` / 100.0)
  片方がもう片方の部分文字列に近いとき強い。`CSlive REC シェイプパンプパワー`
  vs `シェイプパンプPOWER` のようなプレフィクス差に効く。
- token_set_ratio (`fuzz.token_set_ratio` / 100.0)
  トークン集合のオーバーラップ。順序/重複/余計語に強い。

中央値を使うのは外れ値（1 指標だけ極端に高い/低い）を吸収するため。

`similarity_ensemble()` が dict を返すので、呼び出し側は判定に median
だけ使うも良し、ログに生値も全部出すも良し。
"""

from __future__ import annotations

import re
import unicodedata
from typing import TypedDict

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

_NOISE_RE = re.compile(r"[\s/／・、,.。．\-－_]+")

# 実データの `CSlive REC シェイプパンプパワー` のようなプレフィクス/サフィクス付き
# の揺れを、NFKC 正規化の前段で削ぎ落とす。長い方から試すことで、より具体的な
# パターンが先にマッチするようにする（例: `CSlive/REC ` が `CSlive/` に吸収されて
# 後続の `REC` が残る、という事故を防ぐ）。`re.IGNORECASE` で cslive/CSLive の
# 大文字小文字差を吸収する。
_PREFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*CS\s*Live\s*/\s*REC\s+", re.IGNORECASE),
    re.compile(r"^\s*CS\s*Live\s+REC\s+", re.IGNORECASE),
    re.compile(r"^\s*CS\s*Live\s*/\s*", re.IGNORECASE),
    re.compile(r"^\s*REC\s+", re.IGNORECASE),
]
_SUFFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[・/]\s*REC\s*$", re.IGNORECASE),
    re.compile(r"\s+REC\s*$", re.IGNORECASE),
]


class SimilarityScores(TypedDict):
    """5 指標と中央値を返す dict。"""

    levenshtein: float
    jaro_winkler: float
    jaccard3: float
    partial_ratio: float
    token_set_ratio: float
    median: float


def _strip_affixes(s: str) -> str:
    """プレフィクス/サフィクス除去を 1 パスで適用する。"""

    for pat in _PREFIX_PATTERNS:
        new_s = pat.sub("", s, count=1)
        if new_s != s:
            s = new_s
            break
    for pat in _SUFFIX_PATTERNS:
        new_s = pat.sub("", s, count=1)
        if new_s != s:
            s = new_s
            break
    return s


def normalize(name: str | None) -> str:
    """プログラム名を比較用に正規化する。

    - `CSlive/REC ` `CSLive/` `REC ` などの前置語と `・REC` `/REC` などの
      後置語を、先頭で削ぎ落とす（表記揺れを中央値比較の土俵に乗せる）
    - NFKC で全角半角・カタカナ統一（ｼｪｲﾌﾟ → シェイプ）
    - 空白・区切り記号（/, ・, -, 等）を除去
    - 小文字化（ZenYoga / ZENYoga の差を吸収）
    """

    if not name:
        return ""
    s = _strip_affixes(str(name))
    s = unicodedata.normalize("NFKC", s)
    s = _NOISE_RE.sub("", s)
    return s.strip().lower()


def _trigrams(s: str) -> set[str]:
    """長さ 2 以下は全体を 1 トークンとして返す（空集合回避）。"""

    if len(s) < 3:
        return {s} if s else set()
    return {s[i : i + 3] for i in range(len(s) - 2)}


def jaccard_ngram(a: str, b: str, n: int = 3) -> float:
    """文字 n-gram の Jaccard 係数。どちらも空文字なら 1.0。"""

    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if n != 3:
        raise ValueError("only n=3 is supported")
    ga = _trigrams(a)
    gb = _trigrams(b)
    if not ga and not gb:
        return 1.0
    union = ga | gb
    if not union:
        return 1.0
    inter = ga & gb
    return len(inter) / len(union)


def aggregate(scores: list[float]) -> float:
    """中央値を返す。5 指標なら `sorted(scores)[2]` で真ん中を取る。

    偶数件でも真ん中寄り 1 つを使う（`len // 2`）。
    """

    if not scores:
        return 0.0
    return sorted(scores)[len(scores) // 2]


# --- 類似度ゲート閾値 ------------------------------------------------
#
# median 中央値で判定する閾値は `config.settings.Settings.alias_sim_accept` /
# `alias_sim_warn` に移動した（`settings.py` 参照）。hacomono_gateway の
# alias 自動学習と dashboard_query の program_changes 通知の両方で、
# 呼び出し側が settings から値を取り出して本モジュールの similarity_ensemble
# と組み合わせて判定する。
#
#   median >= settings.alias_sim_accept        → 表記揺れとみなす（通常 upsert）
#   alias_sim_warn ≤ median < alias_sim_accept → 怪しい（警告付き upsert）
#   median < settings.alias_sim_warn           → 別物扱い（skip）


def similarity_ensemble(a: str, b: str) -> SimilarityScores:
    """2 つの名前の 5 指標 + 中央値を返す。

    呼び出し側は比較前に自前で normalize() を呼んでから渡してもよいし、
    生文字列を渡してもよい（ここで normalize してから計算する）。
    """

    na = normalize(a)
    nb = normalize(b)
    lev = fuzz.ratio(na, nb) / 100.0
    jw = JaroWinkler.normalized_similarity(na, nb)
    jac = jaccard_ngram(na, nb, n=3)
    partial = fuzz.partial_ratio(na, nb) / 100.0
    token_set = fuzz.token_set_ratio(na, nb) / 100.0
    med = aggregate([lev, jw, jac, partial, token_set])
    return SimilarityScores(
        levenshtein=lev,
        jaro_winkler=jw,
        jaccard3=jac,
        partial_ratio=partial,
        token_set_ratio=token_set,
        median=med,
    )
