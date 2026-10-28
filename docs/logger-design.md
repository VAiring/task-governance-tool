# Logger Design

有効化されたローカル開発用デバッグ基盤の設計。製品の出力と秘密保護は
[logging specification](specification.md#common-debug-logger)、構造上の境界は
[privacy and failure boundaries](design.md#privacy-safety-and-failure-boundaries)を所有者とする。
本書はTask状態・証跡を保持しない。

<a id="applicability"></a>

## 適用条件と責任

正式基盤は `debug_log.py`、`debug_log_values.py`、`debug_log_metrics.py`、`debug_log_sink.py`。
既存SQLite観測を再利用し、通常CLI、書込Setup、usage、レビュー引渡し・MCP要求・
workerの既存admissionを通った処理へ接続する。doctor、明示read-only、レビューの
読取専用表示・basis・finalization check・MCP inspectは保存しない。
既存の個別診断の置換や全メソッドへの一律追加は含めない。
入口は[fixed state resolver](setup-state-design.md#fixed-state-resolver)と
[effective ignore](setup-state-specification.md#effective-git-ignore-preflight)を再利用する。
現用の有効化・設定・信頼の変更は通常Task作業に含まれない。

<a id="enablement"></a>

## 有効化と出力範囲

利用者が物理的な `.taskgov/diagnostics/logger/` と `enabled` を明示的に用意する。
ASCII、BOMなし、末尾LFが1つの `taskgov-logger-v1 debug` または
`taskgov-logger-v1 errors` だけを受理する。初期値はOFF。無効時は一般ログの
引数展開・計測・保存を行わない。通常操作がディレクトリやマーカーを作ることはない。
任意パス、環境変数、公開CLI、背景処理は追加しない。

debugは開始・終了・DB・例外、errorsは失敗終了・失敗DBイベント・例外を記録する。
errorsは成功引数を展開しない。書込直前も物理パス・マーカー・実効ignoreを確認する。
マーカー変更を検知した操作は残りを保存しない。リンク・junction・不一致bindingへの
書込を許可しない。各入口の既存admissionが成功するまでは保存を許可しない。
通常参照の診断書込は明示opt-in時だけの限定例外。公開応答・Task・Receipt・Evidenceに
ログを混ぜず、consoleへのfallbackもしない。

<a id="information-budget"></a>

## 情報量

通常の1操作で最大256イベント、保護済みバッファ512 KiB、1イベント16 KiB（LF込み）。
長いレビュー待機だけは[途中保存](#wait-checkpoints)単位で同じ上限を適用する。
超過時は通常詳細を省略し、失敗イベントを優先する。引数除去後もイベント上限を
超えればそのイベントを破棄する。JSONを途中切断しない。保存可能なら末尾summaryに
理由別破棄件数を記録するが、summary自体も失われ得る。
引数は開始時だけ。戻り値、Task本文、SQL、行、コマンド入出力、環境、自由文をダンプしない。
errorsのspanは失敗終了だけを記録し、例外詳細は処理境界で1回明示記録する。
反復には明示的なaggregateを使い、反復ごとの開始・終了や引数を出力しない。
自動的な例外同一性追跡や全メソッドへの一律計測は行わない。

<a id="argument-policy"></a>

## 引数の記録

`span(function, **arguments)` はfunctionのコードで宣言された引数名だけを採用する。
未知のキー、self/clsは採用しない。通常文字列は不可逆マスク、数値・真偽値は保持、
標準形式 `tg_task_` + 小文字16桁hexのTask IDは保持する。秘密を示す引数名・辞書キー
では数値・IDにもマスクを優先する。自由なラベル文字列を平文保存するAPIは設けない。
password、passphrase、credential、token等に加え、PIN・OTP・TOTP・HOTPや認証・
復旧コードを示す名前も保護する。短い認証名は単語境界で扱い、camelCaseも区切る。
引数とネストした辞書で同じ判定を使い、shipping等の通常の数値名は保持する。
exact str以外の辞書キーは安全に名前判定できないため、対応する値もマスクする。

NULL=`null`、空文字=`empty_string`、4096文字以下の空白のみ=`whitespace`、
未指定=`missing`。大きな空白文字列は通常のマスク済み文字列として扱う。
有限float・256bit以下のintは元の数値、非有限floatは`non_finite`。
巨大intや未知オブジェクトは内容を展開しない。秘密値でもNULL/空/空白の状態は保つ。
任意オブジェクトのstr/repr、property、iteratorは実行しない。
型の確認にも利用者属性やメタクラスの等値比較を使わず、実型で判定する。
exact built-inのlist/tuple/dictだけを深さ3、各階層16要素まで投影する。
辞書はキーも保護したkey/value配列。最大32宣言引数。超過は開始イベントの
args_truncatedで示し、コンテナーの超過は各値のtruncatedで示す。
引数・例外内部辞書・SQLiteキーワード・frame globalsは元辞書を検索しない。
共通のplain_fieldsでexact dictを最大256項目だけ走査し、exact strキーだけの
所有コピーを作ってから検索する。派生型・未知キー・上限超過は採用せず省略し、
引数全体を省略した場合もargs_truncatedで示す。コピーは投影中だけ保持する。
入力validatorで拒否された本文を自動転送しない。

<a id="events-and-correlation"></a>

## APIと相関

```python
from task_governance_tool.debug_log import span, record_exception

def load_task(task_id, limit=20):
    with span(load_task, task_id=task_id, limit=limit):
        try:
            return read_task(task_id, limit)
        except ValueError as error:
            record_exception(error)  # この境界で処理して継続する場合
            return None
```

operation_scopeがなければ何も記録しない。下記の各処理が入口を所有する。新しい入口は
既存project/state admission後に保存を許可する。スコープはDBを開いたりbindingを
推定・修復しない。spanは処理本体を明示的に囲むコンテキストマネージャーであり、
デコレーターやgeneratorへの自動ラップは提供しない。
各行は形式版、UTC日時、発生時現地日付とUTCオフセット、mode、event、level、
invocation_id、pid、thread_idを持つ。spanは一意のspan_idとparent_span_idを持ち、
ContextVarを終了時に戻す。thread・別プロセスへの引継ぎは明示する。
メソッド名・スタック位置はパッケージ内コードに限定し、外部はother/省略する。
コード位置は字句的に投影し、計測中のファイル存在確認やパス解決を行わない。
Task IDは渡された引数として扱い、推定やDB問い合わせを行わない。

反復呼出しは `aggregate(function)` で本体を囲む。同じコード位置の呼出しを
operation単位でまとめ、呼出しごとのspanや自由ラベルを増やさない。

```python
from task_governance_tool.debug_log import aggregate

def process_batch(batch):
    with aggregate(process_batch) as work:
        processed = process_existing_items(batch)
        work.count(processed)  # 本処理ですでに分かっている件数だけを渡す
```

`span(...) as work` も同じ `work.count(n)` を持ち、終了イベントに合計件数を付ける。
どちらも件数は任意で、exact intの非負値を加算する。bool・負値・未知オブジェクトは
変換せず、件数が不確かであることを残す。未指定はnull、明示した0は0。
無効時にも同じ呼出しを使え、件数の投影・計測・保存は行わない。
件数のために結果を再走査したりSQLを追加したりしない。
`aggregate(function, unit="bytes")` は同じ `item_count` をバイト数として扱う。
既定の `count_unit` は `items`。この2種類以外は変換せず `unknown` とし、
集計キーに単位を含めて異なる単位を加算しない。自由な単位名は保存しない。
`work.mark(state)` はコード内の固定分岐を1呼出し・1種類につき最大1回数える。
種類は `local / full_scan / reused / revalidated / unchanged / contended /
deadline / limit / skipped / published / failed` のみ。未知値は無視する。
集計の `states` は観測した種類と呼出し数の辞書であり、全分岐の網羅や成功判定ではない。
1呼出しに複数種類が付き得るので、合計を呼出し回数と解釈しない。
無効時は状態集合も作らず、自由文・引数・ファイル名を分岐名にしない。

<a id="performance"></a>

## 計測

経過時間はperf_counter_nsによるns単位。開始イベントの保護・投入後から本体終了までを
測り、自分の終了イベント処理を含めない。子の計測負荷は含むので親子時間を合算しない。
summaryのbuffer_instrumentation_nsは保護済みレコード構築・JSON化・投入を測る。
呼出し側の引数投影、summary自身、sink I/O、admissionを含まない。全費用は隔離環境で
OFF/ONの呼出し全体を比較する。SQLite計測負荷は既存collector値を別記する。

aggregateは本体のinclusive時間を計測し、operation終了時にコード位置ごとの
`performance.aggregate` を出す。module/function/定義行/件数単位で区別し、operation内だけで
有効なmeasurement_idを振る。同名でも異なる位置は別集計。位置不明はotherにまとめ、
summaryに示す。複雑な親子集計木や反復ごとの詳細は作らない。
既知コード位置は操作内で最大32件だけ再利用し、反復ごとの位置投影を避ける。
呼出し回数、実測回数、累積・最大ns、指定された件数の合計、件数を指定した呼出し数、
正確な件数のある呼出し数・不明な呼出し数、失敗・取消数を残す。
未計測時間はnullであり、0の実測と区別する。件数合計は既知の入力分だけの合計で、
不明呼出しや飽和があれば全体件数の証明にはならない。

1操作32集計まで。保持済み集計は上限後も更新し、最大32集計イベントと1 summaryを
operation終了時に出す。開始・終了数、集計省略、位置不明、観測失敗、63bit非負整数の
飽和をsummaryで示す。errorsでは失敗・取消の呼出しだけを集計して出し、成功集計は
出さない。summaryのpartialと各集計の実測・件数カバレッジを併せて判断する。
このAPI自身の集計・最終出力・保存を含む費用は、admissionからscope終了後までを
囲んだOFF/ON比較で測る。bufferやSQLの内部負荷だけを総追加負荷としない。

<a id="processing-inventory"></a>

### 通常処理の計測箇所と集約範囲

CLIは `main`、業務dispatch、COMMIT後の `apply_post_commit_maintenance` をspanで
区別する。usageは `collect_event` が入口spanを持つ。引数は既存のdispatch投影だけで、
追加した計測箇所は引数を渡さない。以下のaggregateは操作末尾に各1行を出す。
呼出し回数・累積時間・最大時間と件数を併せ、単発の遅延・反復増加・量の増加を判断する。

| 所有module・処理 | 件数の意味と含む処理 |
|---|---|
| state_resolver.resolve_project_state / _inspect_database | 解決全体とDB検査。後者は既存statのDBサイズ（読んだバイト数ではない）。local/full_scan、検証再利用/再検証を区別 |
| storage._validate_current_schema_contract / _validate_current_schema_structure | スキーマ照合・各構造validatorの親。local/full_scanを区別。件数不明はnull |
| storage._fetch_bounded_projection_rows | 実際に取得した行。既存batch loopで加算し、上限拒否を表示 |
| stored_task_validation.validate_stored_task_rows | 検証を試みたTask行。scalar/privacy/Contractの小ループを親に含める |
| state_paths.read_physical_file_bounded / hash_physical_file / _write_all | 実際に読んだ／書いたバイト。既存chunk/short-write loopで加算 |
| git_snapshot.run_git_stream | Git subprocess・読取・待機。成功時の取得バイト、既存timeout分岐。出力内容・argvは保存しない |
| artifact_lock._acquire（呼出し境界） | 待機しないartifact lock取得の時間・競合。取得後の保持時間ではない |
| evidence_projection.canonical_json_bytes | 正規JSON構築の時間と生成バイト。再帰encoderを1回の親計測に含める |
| evidence_publication._publish_evidence_projection | 全体のcapture・Bundle/index公開・再検証。成功公開回数、不要/無効/競合/失敗/上限 |
| viewer.build_viewer_snapshot / render_viewer_html | スナップショットのTask数、既存エンコードで分かるHTMLバイト。各Taskの履歴・Finding集約を親に含める |
| viewer_maintenance._refresh | capture・render・公開・再検証。成功公開回数、不要/無効/競合/失敗/上限 |
| maintenance.run_post_commit_maintenance | Evidence/Viewerと既存backup要求を含む後処理全体。無変更分岐 |
| cli_output.emit_result | 応答構築・JSON縮約・出力。既存binary出力のバイト数。text-only fallbackの件数はnull |
| usage_locator_collection._capture / _pending / _direct / _inventory（呼出し境界） | 各候補選択・locator処理の小ループを集約。件数はnull、下記の走査・試行数と併読 |
| usage_sources.locate_slice | 列挙したdirectory entry数。再開ordinalまでの読み飛ばしも含む。期限/件数上限 |
| usage_ingest_work.IngestWork.run | 既存allowanceを使ったsource試行数。audit/ingestは同じ試行に含む。再検証/再利用/期限/上限 |
| usage_incremental_adapter.scan | header・通常行・oversize drainで読んだバイト。JSON解析・ハッシュ鎖更新を含む。期限/上限 |
| usage_incremental_projection.project_changed | 再集計したcomponent数。全体の構造選択、再利用、再計算を区別。各responseの小ループを親に含める |

これは全関数・全loopのトレースではない。上表の親に含まれる小処理へ別イベントを
追加しない。sourceがない・未実行の処理は出ない。失敗時は既知の途中件数だけの場合が
あり、呼出し結果と併読する。errorsでは例外が外へ伝播した集計だけが対象となり、
内部で処理済みの失敗マークだけでは成功scopeを保存対象に変えない。
SQLは開始時のspan/measurement対応を使う。親子時間はinclusiveで合算しない。
入口admission、logger自身の保存I/O、別workerの時間はこの一覧のCLI時間外であり、
操作全体のOFF/ON比較で費用を確認する。既存の非同期backupにはこの変更で新しい
起動・監視・retryを加えない。拒否されたadmissionとread-onlyは既存どおり保存しない。

`test_debug_log_processing` は実Task・usageの1/30/300件、ファイル反復、列挙再開、
公開不要・競合、閉じた単位/分岐とOFF/ON同一性を検証する。`test_debug_log_entry` と
既存のadoption/SQL/maintenanceテストが公開応答・接続終了・保存境界を補う。

<a id="sql-measurements"></a>

### SQLと結果取得の計測

一般loggerのdebug/errorsだけで `sqlite_sql_trace.py` を使う。SQL本文・そのhash・
引数値・行値を読まず、コード位置（package内module/function/行/bytecode offset/API）を
sql_idにする。最大32フレームの字句的な探索で特定不能なら `other:<API>`。
同じ呼出し位置の動的SQLは同一集計となる。これはSQLの意味や版をまたぐ同一性を
保証するIDではない。入力由来のSQL名・自由ラベルを受け付けない。

invocation_id内でexecution_idを発行し、connection_id、開始前と戻り時のtransaction_id、
親execution_id、API、bindingを保持する。transactionがない場合はnull。
fetch時点のtransactionは別記し、消費中に切り替わった場合はmixedとして単一IDを推測しない。
SQL実行開始時のspan_idとmeasurement_idを保持し、後で別scopeからfetchしても
付け替えない。measurement_idは反復1回ごとのIDではなく上記の通常処理集計との対応。
同じSQL位置が複数の通常処理集計から呼ばれた場合、SQL集計のmeasurement_bindingを
mixed、measurement_idをnullにする。対応なしはnone、観測不能はunknownとし、
実行詳細の対応はその開始時点のまま残す。SQL時間を通常処理時間から自動減算しない。
connectionのshortcutは既定cursorを1つ作りnative Cursor APIを1回だけ呼ぶ。
明示cursorと同じobserverを通り、shortcut再利用・bound method・iteratorも追跡する。
任意のcursor factoryはnativeへそのまま渡し、その呼出しを未計測として数える。
cursor生成の失敗はSQLに到達していない未計測であり、SQL時間0の成功として扱わない。

| 計測 | 意味 |
|---|---|
| execute_ns / execute_max_ns | execute/many/scriptのnative API試行時間。batchやscript内部の件数・時間はunknown |
| fetch_ns / fetch_max_ns / fetch_calls | fetchone/many/all/nextのnative呼出し。executeとは別集計 |
| rows_lower_bound / rows_known | 実際に返された行数の下限と全呼出しで正確に数えられたか |
| wall_lifetime_ns | 実行観測開始から再利用・close・解放・scope終了まで。利用者の待ち時間も含む |
| SQL summaryのinstrumentation_ns | native時間を除くSQL wrapper内の観測負荷。admission、sink I/O、最終集計出力を除く |

API時間はUDF・adapter・converter・row factoryを含むinclusive時間であり、SQLite単体CPU
時間ではない。親SQLからUDFが子SQLを呼べば親時間に子の実行・観測が含まれる。
親子時間やexecute/fetchとwall lifetimeを合算しない。SQL wrapper、transaction observer、
bufferの負荷計測にも包含関係があり、これらを足して総負荷としない。全費用はOFF/ON比較で測る。
trigger内部、script内部、manyの個別パラメーター実行は外側APIに含め、内訳を捏造しない。

fetchmany/allの返却listと成功nextだけは実返却数を数える。fetchoneは標準tuple/Rowの
結果だけNoneをEOFと判断する。nextのStopIterationは、接続時にconverterが無効と確認でき、
取得前後とも標準row/text factoryである場合だけEOFとする。それ以外のStopIterationは
converter/text/row factoryの例外と区別できないため、outcomeとconsumptionをunknownにし、
errorsでも不確かな取得例外として保存する。変換設定の確認はcode-ownedな真偽値と
native factory descriptorだけで行い、converterや行を追加実行・展開しない。
custom row factoryのNoneもunknownにする。fetchoneのrow factoryは取得前後とも
標準であることを確認し、factory自身が設定を変更して返したNoneをEOFと断定しない。
fetch中の例外では途中生成した行を取得できないため、その呼出しの数はunknown。
SELECTの未取得・途中再利用/closeはpartial、EOF確認はcomplete、結果なしはno_rows、
script内部の結果はdiscarded。これらは処理成功やDB変更件数の証拠ではない。
再実行失敗は以前の結果を残す場合も消す場合もあるため、旧観測を閉じ、新しいfetchを
unboundにする。失敗したSQLへ旧結果を帰属させない。scriptは既存cursorの結果を保つので、
script自身のaggregateを閉じても旧結果との対応は維持する。COUNTやprefetchは追加しない。

1操作の独立上限は32 SQL集計、128同時実行観測、64実行詳細。count/累積nsは
63bit非負整数で飽和を表示する。上限後も保持済みSQL集計は継続する。
計測できなかったexecute時間はnullとし、0の実測と区別する。
実行状態の初期値もunknownとし、native成否を集計より先に確定する。観測障害時に
確定していない成否や消費状態を成功・完全取得にせず、既知の失敗・取消を保持する。
超過した集計呼出し、詳細、追跡不能実行、unbound fetch、custom cursor、位置不明、観測失敗を
SQL summaryで報告する。詳細は失敗を優先し、同じ優先度では古い失敗を落とす。
256イベントのうち97枠をSQL詳細・集計・summary、33枠を通常処理の集計・summary、
1枠を全体summaryに予約する。
既存のbyte上限やsink拒否で出力自体が失われ得るため、summaryのpartial=falseでも
全体ログの破棄件数と欠落条件を無視して完全性を主張しない。
errorsでは失敗したSQL/fetchと上記の不確かな取得例外だけを有限保存し、成功詳細と成功集計は出力しない。
日付は実行詳細の終了・失敗観測時、集計はscope終了時。全体の保存条件は変えない。

静的棚卸しは `tests/fixtures/sql_api_inventory.json` を正本fixtureとして
`test_sql_api_inventory` が照合する。SQL所有moduleごとの属性参照を列挙し、
同名executeを持つ2つのGit finalizer所有moduleは非SQLとして明示する。
直接callだけでなくbound aliasへの属性取得を含める。SQL文字列はfixtureにも持たない。
接続棚卸しと組み合わせ、native dispatch・動的dispatchの新規経路を要確認とする。
静的検査はPython全体の型/動的制御の証明ではない。implicit iterationとshortcut再利用は
実SQLiteのnative比較と実運用入口の回帰で補う。独自factory・scopeなし・read-only抑止は
未計測/保存対象外として既存境界を維持する。

<a id="sqlite-integration"></a>

## SQLiteとの接続

`sqlite_transaction_trace.py` と `sqlite_diagnostics.Collector` の既存観測を使う。
record_sinkに渡すのは閉じたラベル・数値・時刻だけ。invocation_idとoperation_idは同一。
SQLや例外オブジェクトを渡さない。一般loggerが有効な処理はそのsinkだけを使い、
SQLite専用マーカーによる二重保存をしない。専用マーカーだけなら固定slot方式を維持する。
専用診断も各入口の既存state/binding admission成功後だけ保存を許可し、
拒否された操作の観測は破棄する。診断のためのDB検査は追加しない。
観測上のtransaction lifetimeはロック保持時間ではない。BEGIN/COMMIT失敗、rollback、
savepoint、scriptの意味は[SQLite diagnostics](specification.md#sqlite-transaction-diagnostics)のまま。
driver呼出し、busy timeout、リトライ、公開エラーを変えない。

簡潔さとI/O負荷の抑制のため、一般イベントも操作末尾まで有限バッファに置く。
長いレビュー待機の閉じた安全境界だけは[途中保存](#wait-checkpoints)を利用する。
全登録connectionのcloseを確認できなければ未保存分を破棄する。接続前の開始も
ハング・クラッシュ時には失われ得る。発生日付ごとにまとめて書き、DB接続中の
ファイルI/Oやイベントごとのignore subprocessを追加しない。

<a id="connection-inventory"></a>

## 接続と入口の対応表

アプリの接続は下表の低層呼出しを通る。`sqlite3.connect` はobserver内の
無効時・観測開始不能時・有効時の3分岐だけに残る。直接呼出し・import/代入別名は
`test_sqlite_observation_coverage` が静的検査し、追加経路を要確認として検出する。
DBの内容やパスを保存して役割を特定しない。共有factoryは既知のファイル名だけで
core/numericalを分類し、private-copyは呼出し側の固定backupラベルを使う。

| 接続所有者 | DB・access | 接続設定・transaction・終了 |
|---|---|---|
| sqlite_connection.connect | core/numerical・create | native既定設定と既存configure、呼出し側の初期化・migration、明示close/closing |
| sqlite_connection.connect_existing | core/numerical・readwrite | mode=rw、元のtimeout、既存configure、BEGIN IMMEDIATE/savepoint/commit/rollback、close/closing |
| sqlite_connection.connect_readonly（snapshot別名含む） | core/numerical/backup原本・readonly | mode=ro/query_only/BEGIN、元のtimeout、読取終了後close/closing |
| backup.restore_managed_backup | backupコピー・create | native既定＋configure、backup aggregate、検証・復元、closing |
| backup.copy_database_snapshot | backupコピー・create | native既定＋configure、backup aggregate、private stage検証、closing |
| backup._copy | backupコピー・create | native既定＋configure、backup後BEGIN・公開前検証、closing |
| async_backup_repository.copy_snapshot | backupコピー・create | native既定＋configure、backup aggregate、live sourceを閉じた後にprivate検証、closing |
| ReviewWaitRepository._connection（DirectRepository含む） | operational・readwrite/readonly | 元のtimeout・isolation_level=None、trusted_schema OFF、BEGIN IMMEDIATE/BEGIN、commit、finally close |

storage、state resolver、Task/Review/Evidence/Viewer/maintenanceのrepositoryは共有
factoryを使用する。usageのlocator/attribution/publication repositoryも同じnumerical
factoryを使う。直接connectを使う独自の設定は低層connect_tracedへ接続し、共有factory
への置換によってisolationやRow factoryを変えない。利用者指定factoryは従来どおり
nativeへ渡し、closeを証明できないためその操作の出力を抑止する。アプリ所有の接続に
独自factoryの使用はない。SQL成功・fetchは一般loggerの上記SQL observerが所有し、
transaction専用sinkへ重複して追加しない。

| 処理入口 | process/thread・scope | 既存admission・出力条件 |
|---|---|---|
| taskgov.py main | 呼出しprocess/thread、CLIと同期maintenanceの終了まで | 既存dispatch_stateful_commandのstate/binding成立 |
| setup.run_setup | CLIと同一scopeを再利用。直接呼出しも1回のSetupだけ | matching resolution、またはfresh/relocationの成功した公開結果。未確定先は保存しない |
| usage_lifecycle.collect_event | Hook process/thread、1eventだけ。元のevent時間予算を維持 | _target成功。登録のみの補助呼出しは親のadmitted scopeを再利用 |
| review_handoff.py main | helper process/thread、1操作だけ。子CLIは独立scope | review_handoff_scope.resolve成功。子processとoperation IDを混同しない |
| review_handoff wait-ended（互換入口） | helper process/thread、1宣言だけ | usage_lifecycle._targetの既存成功を再利用。通常ループへの追加ではない |
| ProjectReviewWaitSession.handle | 常駐MCPの1要求だけ | _location/既存basis reader/handoff resolveの成功 |
| DirectProbe worker（Managed含む） | 専用thread、元のdeadlineで有限の1worker。安全境界で有限の途中保存 | 同一のadmission済み起動identityと所有threadの照合、既存のfresh current_binding、location、handoff resolve。新しいDB検査は追加しない |
| ReviewWaitObserver | 専用thread、次のblocking waitまでの1観測cycle | そのcycleの既存basis確認。fresh admissionがないidle cycleは保存しない |
| worker終了確認 | join後、またはjoin失敗時の独立した短いthread scope | admission済みの同一所有worker/probeの照合を再利用。保存失敗も記録する。旧所有情報を別workerへ転用しない |
| async_backup.worker_main | 独立child process、1回のbackup attemptだけ | managed-backup lock下の既存read_controlと起動basis照合成功。追加DB確認なし。GO前EOF・拒否basisでは保存しない |
| ProjectReviewWaitSession.start_backup_observer / backup launch | 既存MCPの起動admission1回、または新要求の有限launchごとの独立maintenance scope | 既存read_service_target/read_control成功を再利用。idleの10秒周期にはscopeもDB読取も追加しない |
| ProjectReviewWaitSession.record_transport_failure / close（EOF・取消を含む） | 呼出しthread、終了原因の捕捉と既存sessionごとの有限cleanup scope | 同一のadmission済み起動identityと所有threadの照合をjoin前から再利用し、終了原因・timeout・join例外も保存する。新規DB確認なし。読取だけのsessionや照合未成立では保存しない |

workerにはproject/packageだけを渡すscope factoryを明示配線する。ContextVarや
可変collectorを親から共有しない。終了したcollectorはsealし、遅延closeから再開しない。
同一collectorを別threadで使った場合も保存を抑止する。
既存connection/cursorの再利用でも、SQL・fetch/iteration・close・transaction経路で
現在の抑止状態とthread所有者を確認する。別threadやread-only抑止中の利用は、
nativeが成功したか拒否したかにかかわらず元のcollectorを未追跡にし、後からの公開を防ぐ。

doctor・明示read-only・レビューread/material/wait-basis/finalize --check・MCP
inspect/view/direct_statusは、内部の共有factoryを隔離harnessで観測するが実運用では
保存禁止。admission失敗、未close、観測上限でcloseを追えない場合、未計測factory、
クラッシュ、sink拒否も欠落条件であり、成功や無操作を意味しない。scopeのない直接
ライブラリ呼出しや注入stateを使う開発専用入口は現用projectへの書込権限を持たない。
本番入口の除外ではなく、同じ低層observerを注入collectorで検証する例外である。

SAVEPOINT/RELEASE/ROLLBACK TOは名前を保持せず区別する。scriptは途中のcommit・
再開始を観測できないためlifetime=unknown、durationなしとする。BEGIN失敗は寿命を
作らない。native cancellationと失敗を区別し、backupはsource側のaggregateを記録する。
SQLの先頭・制御語間コメントを有限範囲で読み飛ばし、範囲外は推測しない。
active→activeだけでは再開始としない。autocommit=Falseのnative commit/rollback/with
だけが再開始を保証し、autocommit=Trueのno-opやSQLの部分rollbackはIDを維持する。
summaryは登録接続数とDB/一般イベントの欠落数を持つが、summary自体も失われ得る。

<a id="wait-checkpoints"></a>

### レビュー待機の重要イベントと途中保存

`review_wait_runtime.worker_diagnostics` は共通loggerへ渡す型付きStage/Outcomeと
閉じたreasonの投影だけを所有する。独立したlogger・保存先・設定・スレッドは持たない。
既存処理が取得済みのTask、生成wait ID（待機試行）、probe UUID、worker UUIDを検証して
記録する。不明な値はnullとし、実行元metadata・応答・任意文字列は展開しない。
共通のinvocation_id/pid/thread_idにより要求・worker・外側join・service cleanupを結ぶ。
旧版workerにUUIDがなければnullのまま、probeで対応し、終了証拠を補わない。

既存のproject admission後はworker生成前も`request`へ既存CallDiagnosticsの閉じた
stage/reason、local_state/host_mutation/retained_effects/cleanupを投影する。
取得済みの親UUID・Task・要求sequenceで旧試行を区別し、未取得のsequence・wait・workerは
nullのままとする。旧試行のcleanup後、新要求の保存前に旧wait/probe/workerを外し、
旧イベントはそのまま保持する。成功の返却形や公開エラーは変えず、診断用の再読取はしない。
起動・準備失敗は後続のjournal保存やcleanupより先に記録し、二次cleanup失敗を別に残す。
timer設定の既存読取はunsafe/invalid/changed/unavailableとtimezoneの固定理由を
`timer_config`へ残す。本文・パス・設定値や追加の設定読取は保持・実行しない。

開始、レビュー終了観測、timer arm/delete/pauseの試行と結果、通知試行と
accepted/rejected/unknown、receipt観測と打切り理由、制御終了、cleanup、外側join、
終了証拠の保存結果を区別する。serviceのEOF/transport error/取消/requested cleanupは
`service_exit`であり、workerの終了でもOS process exitの確認でもない。
内部で処理済みの例外も、その境界で既存の安全な例外投影と閉じた原因を残す。
観測結果保存やfallbackなど、さらに失敗し得る処理を呼ぶ前に最初の捕捉原因を投影する。
timerの確認済みhost結果はローカル状態の保存前に記録し、保存失敗は`timer_save`で分ける。
保存が失敗した場合の業務上のunknownや再送禁止は変えない。
serviceの終了を伴うprotocol/transport/request失敗は、応答やcleanupに先立ち既存の
同一所有workerでadmissionした短いscopeの`transport`へ投影する。終了理由は最初の原因を
保ち、二次の通信失敗とcleanup失敗は別イベントにする。raw例外の保留バッファや常駐scopeは
作らず、未所有・読取専用・OFFでは保存しない。追加のDB読取は行わない。
通知結果と状態保存、receipt観測とreceipt保存の失敗は別の事実。最初の原因を後続の
cleanupで上書きしない。`worker/returned`や`scope_returned`は制御が戻った意味に限定し、
実際の終了は外側の`join/confirmed`と、別の`exit_save`結果で読む。
送信前guardによる取消・期限切れはその理由を保持し、dispatch関数からのreturnだけを
送信済みと扱わない。同一所有workerの起動時admissionを早期終了・cleanupにも再利用するが、
効果直前のfresh確認や読取専用抑止を省略・緩和しない。

同じ待機identity、stage/outcome/reasonと閉じた観測条件は操作中に1度だけ、組合せは操作全体で最大128件。
同じ要求内の待機更新では以後のイベントだけを新identityへ切り替え、保存待ちの旧イベントを
付け替えず、別probeの同じ遷移も抑止しない。予算・欠落数・保存連番は切替でリセットしない。
変化しない反復は63bitで飽和するpoll数とelapsed_nsだけをcheckpointへ集約する。
checkpointのidentityは保存時の文脈であり、`aggregate_scope=invocation` が示すpoll数・
経過時間・計測値・欠落数は、同一要求中の更新を含む操作全体の集計である。反復ごとのイベント、
自由なラベル、全メソッドの計測を追加しない。
`readiness`は既存のhost終了観測と回答回収後の進行可否を分け、保存済み回答や再admissionを
host終了の新規観測と扱わない。未観測はnull、観測元は閉じたラベルで残す。親turn/chat状態、
親identityと元turnの一致、準備確認から戻った事実も区別する。reviewers_pending/
answers_pending/readiness_unknown/parent_not_terminal/parent_not_idle/dispatch_candidateは
その観測時の条件であり、後のdispatch許可ではない。managedで許される
元turn不一致を失敗に変えない。最新の条件とそのidentityをcheckpointにも残すため、
同じ条件へ戻った場合や遷移枠超過後も最終観測を読み取れる。期限切れはcontrol_endのまま、
正常通知へ読み替えない。errorsでは失敗batchの補足としてのみ最新条件を保存する。
親状態は子の処理前、host終了観測は回答候補の読取・回収前に有界保持する。後続の例外・
取消でも取得済みの観測は失わず、未確認の進行可否はnullとする。observation/endedも実際の
host終了観測だけから出し、進行可能という戻り値や空の観測対象集合から推定しない。
既存256イベント/512 KiBの内側から重要イベント64枠/128 KiBを分離する。
内訳は通常遷移48枠、失敗16枠で、SQL・通常詳細がこの枠を消費・追放することはない。
重要枠超過は新しい記録を落として最初の原因を保持し、important_limitを残す。
残る192枠/384 KiBでも既存SQL97枠・処理集計33枠・summary1枠の予約を維持する。
待機scopeではwait.checkpointがそのsummary枠を使い、正常詳細が上限に達しても補足情報を残す。
上限の一律拡大は行わない。bind前の一般詳細が予約分を使っていれば有限に削減して
buffer_limitへ数える。errorsでは成功遷移を出さない既存方針を維持する。

重要枠の128 KiB中16 KiBを、`debug_log_wait_sql`の取得済み接続文脈に予約する。
最大8接続のopen、成功・観測済みbegin、最初の失敗とそのidentity、同じtransactionの
観測済みend、closeだけを有限保持する。後のtransactionや失敗BEGINで開始終了を差し替えず、
script等で寿命が不明になった境界はnullとする。Collectorのnative close通知は後続endの
観測後に文脈を破棄する。失敗接続は最初の失敗時とこのclose通知時に`wait.db_failure`として
既存の失敗16枠へ投影する。open失敗ではcloseを補わず解放する。通知はメモリ処理のみで、
追加のnative callや保存は行わない。重要イベントのbyte上限は残る112 KiBである。
これにより通常SQL詳細に押し出されても接続/transaction IDと既知の開始・終了を辿れる。
errorsでもこの成功境界は失敗の文脈としてだけ保存する。観測できない開始・終了はnull、
coverageはpartial、競合相手はunknownで、ロック所有者や他processを推定しない。
文脈枠超過はdb_context_limitを残し、保持できなかった失敗は文脈nullで記録する。
byte上限・重要枠超過・sink失敗・未確認closeの欠落規則は同じで、保存成功を推定しない。

checkpointは全登録接続のclose、未追跡接続なし、同一thread、既存admission成立、
read-only抑止なしを確認した場合だけ、既存日別sinkへまとめて1回試行する。
DB接続中なら延期し、ログのための接続・再読取・SQL・transactionは増やさない。
通常観測境界では初回と前回保存試行から60秒以上経過したときだけ保存する。
timer/sendの効果境界、最初の失敗・cleanup前、外側join確認では早い保存を要求できる。
1scopeは途中保存最大64回と終了時1回。定期flushタイマー、待機thread、再試行はない。
SQL/処理集計は終了まで同じ有限collectorに保持し、途中保存でseal/resetしない。
接続・SQL・measurement IDの相関とoperation全体の集計意味は変えない。

各batchは既存の安全な日別追記を使い、未保存分を先に取り外す。
途中失敗・部分writeを再送しない。後続checkpointは連番、前回written/unknown、
理由別欠落数、partial coverageを持つ。容量・競合・無効化・I/O失敗でも本処理は変えない。
errorsでも失敗を含むbatchのcheckpoint補足情報を保存し、成功遷移は引き続き抑止する。
末尾の保存結果をその同じ末尾で証明することはできず、summaryも失われ得る。
途中保存済み行は後のprocess強制終了で取り消さないが、未保存の終了行の欠落を正常終了と
解釈しない。電源断時の永続性保証は既存sinkと同じ。ログは診断だけであり、Task完了・
worker終了・効果確定・復旧許可の正本ではない。

`test_review_wait_logging` は長い待機、SQL飽和、秘密投影、OFF/errors/read-only、
保存障害、並行scope、隔離子process終了、managed/legacy・receipt・join・EOFを検証する。
既存logger/SQLite/待機回帰と併せ、OFF/ONの結果、効果回数、読取回数、費用を比較する。

<a id="exceptions-and-masking"></a>

## 例外と保護

例外詳細はrecord_exceptionで明示記録する。未処理例外はスコープ入口で1回記録する。
spanはfailed/cancelledの終了だけを記録する。メッセージは種類にかかわらず省略する。
初期基盤は既知builtin例外種別とパッケージ内の最大20フレームのmodule/function/行番号
に限定する。走査は64フレームまで。原因例外・source行・locals・任意属性は展開しない。
tracebackとSQLite数値エラーは組込みの例外領域から取得し、サブクラスのpropertyを実行しない。
メッセージadapterや原因連鎖は基盤完成の条件ではない。
raw引数・例外・tracebackをバッファやLogRecordへ保存しない。保護不能なら省略する。
logging失敗は本来の戻り値・例外を変更しない。
scopeとspanはclassのコンテキストマネージャーとし、終了処理も例外のtraceback propertyを
読み書きしない。無効時・read-only時にも元の例外オブジェクトをそのまま伝播する。

<a id="daily-files"></a>

## 日別保存

UTF-8、BOMなし、LFのJSONL。ファイル名は `taskgov-debug.log.YYYY-MM-DD`。
当日にも末尾日付を付け、遅延イベントは保持対象内なら発生時現地日付へ追記する。
空の日のファイル、日付なしcurrent、renameはない。
thread lockと専用OS artifact lockをこの順で取得し、非待機1回で競合を断念する。
lockファイルの存在は所有の証拠にしない。再確認・世代整理・容量検査・追記・closeまでを囲む。
logger.lockは共有artifact lockのrequire_single_linkを有効にし、最初の書込前と
取得後に開いたdescriptorとパスのリンク数1を確認する。他の利用者の既定契約は変えない。
os.writeの非バッファ追記で、不完全な末尾行はLFで分離し切り詰めない。
読み手は不正行を不完全記録として扱う。電源断時の永続性は保証しない。
1ファイル100 MiB、当日を含む最新3日付世代、合計300 MiBを上限とする。
書込時、実在する既知ログ日付・書込日・現在の現地日付の和集合から新しい3日付を
保持対象にする。当日が未作成でも1世代として数え、時計が戻った場合は既知の新しい
日付を優先する。保持対象外の遅延イベントは旧世代を再生成せず、新世代を追い出さない。
日次容量に達しても同日の別世代は増やさない。末尾LF補完に必要な実byte数も容量に含む。

厳密な名前と実在日付を持つ物理的な単一リンクファイルだけを管理する。
ディレクトリ全体を最大128エントリで検査してから、保持対象外を古い順に削除する。
開始時に4世代以上あっても整理でき、旧世代は容量超過でも本文を読まずに削除できる。
既知名のリンク・非regularファイルや検査不能があれば整理・出力を断念する。
マーカー、ロック、似た名前、無関係なファイル、子ディレクトリ内の退避資料は削除しない。
各削除・追記の直前にも物理パスと同一性を再確認する。ログ全本文は走査しない。
保持対象の外部改変による容量超過は切り詰めず、保存を断念する。削除・検査失敗や競合は
本処理の結果を変えず、待機・再試行・追加DB接続をしない。OFF・read-only・admission失敗時は
整理も行わない。容量保証は管理可能な既知ログだけに適用し、削除不能ファイルや
外部ファイルの強制縮減は保証しない。
ログはcoreバックアップ、配布物、source commitから除外する。

<a id="implementation-boundaries"></a>

## 実装境界

valuesは記録前投影、metricsは件数と有限の反復集計、debug_logは相関・span・aggregate・
有限バッファ・SQLite adapter、sinkは固定パスadmissionと日別保持・追記を所有する。
業務エラー、transaction cleanup、Task入力検査の
所有者は変更しない。ログのためのDB接続・migration・ネットワーク・背景処理を追加しない。

<a id="verification"></a>

## 検証

短い呼出し例、OFF/ON/errors、数値と状態、ネスト秘密と未知オブジェクト、開始終了と
例外不変、実SQLite失敗・close、有限集計・件数・欠落・SQLとの対応を隔離検証する。
日跨ぎ・遅延・時計巻戻り、過剰世代整理、thread/process競合、途中行と容量・削除失敗・
書込失敗、マーカー変更とunsafe path、公開出力とdoctor/read-onlyも確認する。
OFF/ONの全体時間と出力量を比較し、計測・集計・保存を含む追加負荷を確認する。
文書・配布整合を確認し、レビューはTier 2とする。
