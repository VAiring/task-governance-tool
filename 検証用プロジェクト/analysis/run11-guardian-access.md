# Run11 Guardian と状態更新権限の照合

2026-09-12。調査対象は固定候補 `db4df94` の完了済み Run11。
本書は実施者向けの分析であり、Skillの規則、設定の適用指示、追加の完了条件ではない。
既存の凍結資料・計測結果は更新していない。設定案は未適用で、再計測もしていない。

## 結論

観測された Guardian 26件は、すべて `exec_command` の
`require_escalated` 要求に対応する。内訳はTask・証跡更新19件とGit操作7件。
通常の説明読取や `task context` に審査が付いた記録ではない。
Skillあり側では `.agents` 以下の状態更新が現在の読取専用境界と衝突する。
初回の `internal_error` の直接原因までは確定できない。

改善候補は、対象プロジェクトの canonical `state/` だけに書込みを認める
ホスト側の限定プロファイルである。Skill全体、Python全般、Git全般の許可ではない。
現在の管理設定・Windows実装がこの例外を受け入れるかは未確認であり、
適用可否や審査削減量を実証済みとは扱わない。

## 実操作と審査の対応

根拠は保存済みroot公開操作とGuardianの明示的な `APPROVAL REQUEST`、
`task_complete` の公開判定。Guardianへ渡った会話本文や私的推論は転載しない。
以下の行番号は後述の G（Guardian）の物理JSONL行。全26件の結果は `allow`。

| 操作 | R1 | R2 | R3 | Gの要求行 |
|---|---:|---:|---:|---|
| task add | 1 | 1 | 1 | 8,117,205 |
| review target set | 2 | 1 | 1 | 31,52,140,227 |
| verification receipt add | 1 | 1 | 1 | 63,151,237 |
| review receipt add | 2 | 2 | 2 | 74,85,162,173,248,259 |
| task complete | 1 | 1 | 1 | 106,194,280 |
| git add | 2 | 1 | 1 | 20,41,129,216 |
| git commit | 1 | 1 | 1 | 95,183,269 |
| 合計 | 10 | 8 | 8 | 26件 |

1回の `functions.exec` が複数のshell操作を呼ぶため、外側のツール回数と
審査件数は一致しない。特に各段階のReceipt2件は同一応答内のループでも個別審査された。
R1のtarget再設定は途中の実装修正に伴うもので、承認拒否による再試行ではない。

Skill CLI全27件のうち、19件が上表の成功した状態更新、7件が通常権限で成功した
context読取、1件が最初のadd失敗である。Wの32/34行で初回失敗と空のcontext、
37行で権限説明読取、42/44行で同じTask引数による拡張実行と成功を確認した。
doctorは実行されていない。sanitized error、後続成功、現在のACLだけでは
当時のOS拒否・一時障害・他の内部原因を分離できない。

Skillなし側も `.git` 操作で7回の明示的拡張要求がある
（Cの115,164,245,270,432,463,531行）。Guardianの子記録・使用量が未観測であることは、
要求や審査がなかったことを意味しない。

## 使用量は分離する

最終累積meterの `usage.development_usage` と `usage.guardian_usage` を使用。
中間段階の累積値を再加算しない。通常子は観測されていない。

| 全3段階 | Skillあり | Skillなし |
|---|---:|---:|
| 開発root総トークン | 15,549,534 | 7,941,845 |
| 開発root応答 | 107 | 63 |
| Guardian総トークン | 1,512,519 | null（未観測） |
| Guardian応答 | 26 | null（未観測） |

あり側の観測合算は17,062,053。なし側をGuardian=0として合算比較しない。
cachedはinput、reasoningはoutputの内数。実費や19件の状態更新だけに帰属する
限界トークン費用は本調査で算出していない。

## 実行設定・モデル定義・grant

両rootの全3段階の `turn_context` は Astra/medium、`on-request`、
`approvals_reviewer=auto_review`、workspace-write、network無効。
Skillありの実効filesystem記録ではプロジェクトはwriteだが `.agents` と `.git` はreadで、
stateへの狭いwrite例外はない。これは当時の記録であり、現在のデフォルトからの推測ではない。

調査時のstate ACLには継承されたwrite系Denyが1件あり、ACL保護フラグはfalseだった。
現在のACL照合に限る観測で、当時の実行トークンによる有効アクセス判定ではない。
ACLは変更していない。

調査時に取得した `models_cache.json` の `fetched_at` は
`2026-09-12T14:16:57.267398400Z`。Astraの
`node_repl_auto_review_required=true` を確認したが、Run11終了後の取得である。
Run開始時点の同じ値や、execの個別審査をこのNode REPL用フィールドが起こしたことは
証明できない。26件の直接観測は明示的なexec権限拡張とauto_reviewの組合せで説明でき、
モデル固有設定を追加の確定原因にする必要はない。

Wの42行以降では同じtaskgov実行prefixを継続提示し、Gitも同じadd/commit prefixを
提示している。それでも上表の審査が発生した。prefix提案は永続grantの発行証明ではない。
保存された有効grantの発行・失効記録は本資料から確認できず、
「grantが再利用不能」「LLMが有効grantを無視した」とは断定しない。
反対に、prefixを再提示すれば審査がなくなるとも説明できない。

## 限定設定案（別途承認・未適用）

対象は `C:/WorkSpace/orderdesk-01/.agents/skills/task-governance-tool/state`。
通常のTask/証跡更新が必要とするDB、rollback journal、既存の限定された
Evidence/Viewer/backup保守用ファイルの作成・更新・rename・削除をこの配下に限定する。
DB1ファイルだけの許可ではtransactionや原子的な公開に不足する。
権限上は任意のsandboxed commandがstateを書けるため、CLIだけを認証する仕組みではない。
state内の誤更新リスクを受け入れる必要がある。

以下は公式permission-profile形式による候補であり、現在の設定に追記する手順ではない。
管理側の追加制約を維持し、旧sandbox設定との競合とWindowsの対応可否を
適用前に確認する必要がある。許可されなければ現行の個別承認を維持する。

```toml
default_permissions = "orderdesk-task-state"
approval_policy = "on-request"
approvals_reviewer = "auto_review"

[permissions.orderdesk-task-state]
extends = ":workspace"

[permissions.orderdesk-task-state.filesystem]
"C:/WorkSpace/orderdesk-01/.agents/skills/task-governance-tool/state" = "write"

[permissions.orderdesk-task-state.network]
enabled = false
```

差分はstateへの狭いwrite例外だけを意図する。Skillソース・config・他の.agents・
.codex・.gitは読取専用のまま。Runner起動、setup、インストール、ネットワーク、
Git操作の承認を兼ねない。管理要件の緩和、auto-review停止、ACLの直接編集、
state移設、広いPython/prefix許可は提案しない。
公式資料ではprofilesはBetaで、旧 `sandbox_mode` 等と併用できず、Windowsでは
実装が強制できないsplit policyは拒否される。現環境での適用可否は未検証。

これが適法・有効に適用され、ほかの審査条件がなければ、19件の状態更新は
境界越えを要しなくなる可能性がある。Git7件やモデル別の別審査まで消える保証はない。
失敗予防や削減率の確認には別途承認された試行が必要で、今回のTaskでは実施しない。
既存のexecution-access案内はこの区別を既に持ち、追加のSkill文言や定常probeは不要。

## 出典と検証範囲

- [固定候補・実行記録](../runs/run-11/launch-state.json)、
  [既存結果](../runs/run-11/RESULTS.md)、各段階の `releaseN-orderdesk-01-runtime-audit.json`。
- [あり最終meter](../measurements/run11-release3-orderdesk-01.json)、
  [なし最終meter](../measurements/run11-release3-orderdesk-02.json)。
- W: ローカルsessionの `rollout-2026-09-12T17-19-52-01a094b3-978d-7322-9126-18fc4fd78417.jsonl`。
  SHA256 `4157b34ead85f4936c6ed48ff0504eb39536e2e129cd3efb6144bbd790401190`。
- C: `rollout-2026-09-12T17-19-58-01a094b3-aef3-7842-94a8-786676c43da6.jsonl`。
  SHA256 `ccaa39f66ccacb2a476f86d4ec7d574fe5c47bfbd77a72fc86a8bd833375cd90`。
- G: `rollout-2026-09-12T17-21-15-01a094b4-d927-7a13-b682-42c1fcc1fd8a.jsonl`。
  SHA256 `5612f2aa9d71bc4b784151d3f16a02cbbc283cc584c5a4b0247997bc8ef52443`。
  いずれもローカルの `C:/Users/maste/.codex/sessions/2026/09/12/`。生ログは本書へ複製しない。
- 現行製品の必要アクセス: [Host Execution Access Guidance](../../docs/task-operation-specification.md#host-execution-access-guidance)、
  [journal/connection](../../docs/design.md#journal-and-connection-rules)、
  [post-commit保守](../../docs/setup-state-design.md#post-commit-coordinator)。
- 2026-09-12取得の公式資料:
  [保護パス](https://learn.chatgpt.com/docs/agent-approvals-security#protected-paths-in-writable-roots)、
  [auto-reviewの発生条件と限界](https://learn.chatgpt.com/docs/sandboxing/auto-review)、
  [permission profilesとWindows制約](https://learn.chatgpt.com/docs/permissions)。
  現行公式仕様と実行当時の記録は別の証拠として扱う。

照合は公開操作、明示的審査要求と判定、設定metadata、使用量に限定した。
現在のプロジェクトTask状態の開始・証跡記録を除き、権限、製品、実験成果物は変更しない。
この調査は障害の再現試験や、全Guardian挙動の証明ではない。
