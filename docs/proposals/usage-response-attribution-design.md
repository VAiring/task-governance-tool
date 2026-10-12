# 応答単位の使用量帰属の設計参照と調査記録

応答単位の使用量帰属の設計は、[正式な条件付き設計書](../usage-response-attribution-design.md)へ整理した。機能の境界・責務・保存・検証の所有者は同設計書であり、この旧案を競合する実装契約として使用しない。現行製品への適用条件は[適用条件](../usage-response-attribution-design.md#applicability)を参照する。

以前このパスを参照していたTaskの入力を辿れるよう、参照入口を保持する。開始・完了の実装Task `tg_task_b8c50479a27693c8`、停止・再開の実装Task `tg_task_a7d1acb311043e7c` の範囲・進捗は公開CLIを正本とする。新設計書への案内はTaskの受入条件や順序を変更しない。

- [応答境界と例外](../usage-response-attribution-design.md#boundary-rules)
- [元呼出しと数値応答の対応](../usage-response-attribution-design.md#operation-response-correlation)
- [成功応答の読取](../usage-response-attribution-design.md#transport-and-success)
- [保存と再収集](../usage-response-attribution-design.md#persistence-and-replay)
- [受入検証](../usage-response-attribution-design.md#verification)
- [実装担当が確定する技術条件](../usage-response-attribution-design.md#implementation-qualification)

以下の調査記録は非規範の根拠資料であり、host対応の保証、現在の製品状態、Task完了証跡ではない。

## 調査から採用する根拠と限界

2026-10-13の過去Run調査では、日付ディレクトリ2026/10/05～12から、このrepo内をcwdとする992物理segment・980threadを選択した。現在チャットと無関係なprojectは除外した。これはイベント時刻の厳密な期間指定ではない。

- 23,959個の一意な数値応答を観測した。18,391個の外側呼出しのうち18,390個では、呼出しと最初の同じ`call_id`の結果との間に同一turnの数値応答が一つあった。残る一つは中断turnで数値観測がなかった。この順序は対応候補の根拠であり、ホストが全形式で順序を保証する証明ではない。
- 現行抽出器が読めた117操作のうち112操作はwait相当の呼出し結果内にあった。数値session handleから元呼出しを追える実例があり、結果受領応答をそのまま起点にすると誤る。117件は全成功操作の母集団ではなく、既存抽出器が読める形式に偏る。
- E2の別projectの原記録では、Python起動時の既知警告がJSONの前にあるため、現行境界抽出器が開始成功を取り出せなかった。診断用に既知の先頭警告を除くと、既存の閉じた登録参照解決から開始の構造を読めた。E2の全計測が欠測したことを意味しない。
- 同一応答の複数の独立した外側呼出しは標本では未観測。内側の複数実行の完全な対応付け、全非同期経路、coreへの全件再照合、障害注入は調査では完了していない。`Promise.all`等の記述だけを並列成功の証拠にしない。

確認版は調査開始時`6af9f8405ec79fdfe0d5132fb03c5cf2aea9faea`、集約時`3e531f89ff825e7c4d20f3bd84a64e9b7f45808a`。採取瞬間のHEAD自体は記録していない。この間の`usage_adapter.py`・`usage_turn_adapter.py`・`usage_values.py`に差分はなかった。調査用の試作解析を製品parserとして流用したり、標本比率を対応精度として表示したりしない。
