#!/usr/bin/env python3
"""alias 類似度ゲートの閾値検証スクリプト。

Issue #1 で想定される「同一（表記揺れ）」「別物（プログラム差し替え）」
のペアを並べて、5 指標 + 中央値を出力する。

末尾で「同一群の中央値最小」と「別物群の中央値最大」を見てマージンを
計算し、clean cut できる閾値案を提案する。本番の `_ALIAS_SIM_ACCEPT` /
`_ALIAS_SIM_WARN` はこの出力を元に調整する。

実行方法（dev-admin から）:
    cd /workspace/toolbox/tools/central-sports-web
    python3 scripts/verify_similarity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# local import できるようにパス挿入
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

from app.utils.program_similarity import normalize, similarity_ensemble  # noqa: E402

# 本番で採用中の閾値。`config/settings.py` のデフォルトと一致させる。
# 同一群 min=0.636 / 別物群 max=0.571 の間に挟まる値（margin +0.065）。
ACCEPT_THRESHOLD = 0.61
WARN_THRESHOLD = 0.576


def _classify(med: float) -> str:
    """median から実運用と同じロジックで判定名を返す。"""

    if med >= ACCEPT_THRESHOLD:
        return "accept"
    if med >= WARN_THRESHOLD:
        return "warn"
    return "skip"

# (name_a, name_b, expected_label, 備考)
# 備考で意図を明記（同一=表記揺れ/別物=差し替えのどちら軸での cut を見たいか）
PAIRS: list[tuple[str, str, str, str]] = [
    # --- 同一群（表記揺れ。学習を通したい） ---
    ("シェイプパンプ", "シェイプパンプ", "same", "完全一致"),
    ("ｼｪｲﾌﾟﾊﾟﾝﾌﾟ", "シェイプパンプ", "same", "半角/全角カナ"),
    ("CSLive/REC ZenYoga", "CSLive/ＺｅｎＹｏｇａ", "same", "全角/半角英字 + 大文字小文字"),
    ("健康太極拳・REC", "健康太極拳", "same", "接尾辞 REC の有無"),
    ("シェイプパンプパワー", "シェイプパンプPOWER", "same", "カナ/英字混在"),
    # --- 同一群（プレフィクス付きバリアント。B 案で吸収したい） ---
    ("CSlive REC シェイプパンプパワー", "シェイプパンプPOWER", "same", "CSlive+REC プレフィクス（AA747）"),
    ("CSLive/REC ファイトアタックBEAT・REC", "ファイトアタックBEAT", "same", "CSLive/REC 前置 + 接尾 REC"),
    ("CSLive/REC シンプルエアロ", "REC シンプルエアロ", "same", "CSLive/ 前置の有無"),
    # --- 別物群（差し替え/全く別のプログラム。学習を弾きたい） ---
    ("ビュープロ/アームス", "ビュープロ/レッグライン", "diff", "同シリーズ別部位"),
    ("フィールヨガ", "パワーヨガ", "diff", "ヨガの種別違い"),
    ("シンプルエアロ", "シンプルステップ", "diff", "エアロ / ステップ"),
    ("ヨガ", "フィールヨガ", "diff", "短名と fused"),
    ("シンプルエアロ", "ZUMBA", "diff", "全く別カテゴリ"),
    # --- 別物群（プレフィクス付きでも別物。誤学習させたくない） ---
    ("CSLive/ZenYoga", "フィールヨガ", "diff", "progcd 再利用想定（Live → 別ヨガ）"),
    ("CSLive/シンプルエアロ", "CSLive/ZUMBA", "diff", "同プレフィクスでも全く別物"),
    ("CSlive REC ビュープロ/アームス", "ビュープロ/レッグライン", "diff", "同プレフィクスでも別部位"),
]


def _fmt_score(x: float) -> str:
    return f"{x:.3f}"


def main() -> int:
    print("=" * 160)
    print(
        f"alias 類似度ゲート 検証（5 指標）  "
        f"ACCEPT>={ACCEPT_THRESHOLD}  WARN>={WARN_THRESHOLD}"
    )
    print("=" * 160)
    print(
        f"{'label':5s} {'name_a':36s} {'name_b':36s} "
        f"{'lev':>6s} {'jw':>6s} {'jac3':>6s} {'part':>6s} {'tset':>6s} {'med':>6s}  "
        f"{'判定':8s} {'期待一致':5s}  備考"
    )
    print("-" * 160)

    same_medians: list[float] = []
    diff_medians: list[float] = []
    rows: list[tuple[str, float, str]] = []
    mismatches: list[str] = []

    for a, b, label, note in PAIRS:
        scores = similarity_ensemble(a, b)
        med = scores["median"]
        if label == "same":
            same_medians.append(med)
        else:
            diff_medians.append(med)
        decision = _classify(med)
        # 期待一致: same -> accept または warn が期待、diff -> skip が期待
        if label == "same":
            match = decision in ("accept", "warn")
        else:
            match = decision == "skip"
        match_flag = "OK" if match else "NG"
        if not match:
            mismatches.append(f"{label:5s} {a} / {b}  med={med:.3f}  decision={decision}")
        print(
            f"{label:5s} {a:36s} {b:36s} "
            f"{_fmt_score(scores['levenshtein']):>6s} "
            f"{_fmt_score(scores['jaro_winkler']):>6s} "
            f"{_fmt_score(scores['jaccard3']):>6s} "
            f"{_fmt_score(scores['partial_ratio']):>6s} "
            f"{_fmt_score(scores['token_set_ratio']):>6s} "
            f"{_fmt_score(med):>6s}  "
            f"{decision:8s} {match_flag:5s}  {note}"
        )
        rows.append((label, med, f"{a} / {b}"))

    print("-" * 140)
    print()
    print("【正規化後の表示（デバッグ用）】")
    for a, b, label, _note in PAIRS:
        print(f"  {label:5s} {normalize(a)!r:40s} vs {normalize(b)!r:40s}")
    print()

    print("=" * 140)
    print("【マージン分析】")
    print("=" * 140)
    if not same_medians or not diff_medians:
        print("同一群 or 別物群が空です。ペア定義を見直してください。")
        return 1

    same_min = min(same_medians)
    same_max = max(same_medians)
    diff_min = min(diff_medians)
    diff_max = max(diff_medians)
    print(f"同一群  median: min={same_min:.3f}  max={same_max:.3f}  (n={len(same_medians)})")
    print(f"別物群  median: min={diff_min:.3f}  max={diff_max:.3f}  (n={len(diff_medians)})")

    margin = same_min - diff_max
    print()
    if margin > 0:
        mid = (same_min + diff_max) / 2
        warn_lower = max(diff_max, mid - 0.05)
        accept_lower = min(same_min, mid + 0.05)
        print(f"clean cut OK: 同一最小 ({same_min:.3f}) > 別物最大 ({diff_max:.3f})  margin={margin:.3f}")
        print("  推奨閾値:")
        print(f"    _ALIAS_SIM_ACCEPT = {accept_lower:.2f}   (同一最小をギリギリ通す)")
        print(f"    _ALIAS_SIM_WARN   = {warn_lower:.2f}   (別物最大をギリギリ弾く)")
        print(f"  中央値 {mid:.3f} を挟んで ±0.05 の warning band を置く設計:")
        print(f"    >= {mid + 0.05:.2f}  → upsert")
        print(f"    >= {mid - 0.05:.2f}  → upsert_warning")
        print(f"    <  {mid - 0.05:.2f}  → skip")
    else:
        print(f"clean cut NG: 同一最小 ({same_min:.3f}) <= 別物最大 ({diff_max:.3f})  overlap={-margin:.3f}")
        print("  指標を追加する / 正規化を調整する / ペアを見直す 等の対応が必要。")
        print()
        print("【オーバーラップ一覧】")
        overlap_lo = min(same_min, diff_max)
        overlap_hi = max(same_min, diff_max)
        for label, med, pair in sorted(rows, key=lambda r: r[1]):
            marker = "<" if overlap_lo <= med <= overlap_hi else " "
            print(f"    {marker} {label:5s} med={med:.3f}  {pair}")

    print()
    print("=" * 160)
    print("【採用閾値での判定結果】")
    print("=" * 160)
    if mismatches:
        print(f"期待と異なる判定が {len(mismatches)} 件あります:")
        for line in mismatches:
            print(f"    NG {line}")
        return 2
    print(f"全 {len(PAIRS)} ペアで期待判定と一致 (ACCEPT>={ACCEPT_THRESHOLD} / WARN>={WARN_THRESHOLD})")
    # 同一最小が ACCEPT を超えているか / 別物最大が WARN 未満か（clean cut）
    if same_min >= ACCEPT_THRESHOLD and diff_max < WARN_THRESHOLD:
        print(
            f"  clean cut 成立: same_min={same_min:.3f} >= ACCEPT={ACCEPT_THRESHOLD}"
            f" / diff_max={diff_max:.3f} < WARN={WARN_THRESHOLD}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
