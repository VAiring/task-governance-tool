# Logger Design

有効化されたローカル開発用デバッグ基盤の設計。製品の出力と秘密保護は
[logging specification](specification.md#common-debug-logger)、構造上の境界は
[privacy and failure boundaries](design.md#privacy-safety-and-failure-boundaries)を所有者とする。
本書はTask状態・証跡を保持しない。

<a id="applicability"></a>

## 適用条件と責任

正式基盤は `debug_log.py`、`debug_log_values.py`、`debug_log_sink.py`。
既存SQLite観測を再利用し、通常CLIの既存state admissionを通った操作だけに接続する。
Setup、doctor、明示read-onlyは対象外。usage lifecycleはSQLite専用診断のままとする。
レビュー待機・バックアップの個別診断や全メソッドへの一律追加は含めない。
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
書込を許可しない。CLIの既存state admissionが成功するまでは保存を許可しない。
通常参照の診断書込は明示opt-in時だけの限定例外。公開応答・Task・Receipt・Evidenceに
ログを混ぜず、consoleへのfallbackもしない。

<a id="information-budget"></a>

## 情報量

1操作で最大256イベント、保護済みバッファ512 KiB、1イベント16 KiB（LF込み）。
超過時は通常詳細を省略し、失敗イベントを優先する。引数除去後もイベント上限を
超えればそのイベントを破棄する。JSONを途中切断しない。保存可能なら末尾summaryに
理由別破棄件数を記録するが、summary自体も失われ得る。
引数は開始時だけ。戻り値、Task本文、SQL、行、コマンド入出力、環境、自由文をダンプしない。
spanは失敗終了だけを記録し、例外詳細は処理境界で1回明示記録する。
自動的な例外同一性追跡・反復集約は初期基盤に含めない。

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

operation_scopeがなければ何も記録しない。通常CLIが入口を所有する。新しい入口は
既存project/state admission後に保存を許可する。スコープはDBを開いたりbindingを
推定・修復しない。spanは処理本体を明示的に囲むコンテキストマネージャーであり、
デコレーターやgeneratorへの自動ラップは提供しない。
各行は形式版、UTC日時、発生時現地日付とUTCオフセット、mode、event、level、
invocation_id、pid、thread_idを持つ。spanは一意のspan_idとparent_span_idを持ち、
ContextVarを終了時に戻す。thread・別プロセスへの引継ぎは明示する。
メソッド名・スタック位置はパッケージ内コードに限定し、外部はother/省略する。
コード位置は字句的に投影し、計測中のファイル存在確認やパス解決を行わない。
Task IDは渡された引数として扱い、推定やDB問い合わせを行わない。

<a id="performance"></a>

## 計測

経過時間はperf_counter_nsによるns単位。開始イベントの保護・投入後から本体終了までを
測り、自分の終了イベント処理を含めない。子の計測負荷は含むので親子時間を合算しない。
summaryのbuffer_instrumentation_nsは保護済みレコード構築・JSON化・投入を測る。
呼出し側の引数投影、summary自身、sink I/O、admissionを含まない。全費用は隔離環境で
OFF/ONの呼出し全体を比較する。SQLite計測負荷は既存collector値を別記する。

<a id="sqlite-integration"></a>

## SQLiteとの接続

`sqlite_transaction_trace.py` と `sqlite_diagnostics.Collector` の既存観測を使う。
record_sinkに渡すのは閉じたラベル・数値・時刻だけ。invocation_idとoperation_idは同一。
SQLや例外オブジェクトを渡さない。一般loggerが有効なCLIはそのsinkだけを使い、
SQLite専用マーカーによる二重保存をしない。専用マーカーだけなら固定slot方式を維持する。
専用診断もCLI・usage lifecycleの既存state/binding admission成功後だけ保存を許可し、
拒否された操作の観測は破棄する。診断のためのDB検査は追加しない。
観測上のtransaction lifetimeはロック保持時間ではない。BEGIN/COMMIT失敗、rollback、
savepoint、scriptの意味は[SQLite diagnostics](specification.md#sqlite-transaction-diagnostics)のまま。
driver呼出し、busy timeout、リトライ、公開エラーを変えない。

簡潔さとI/O負荷の抑制のため、一般イベントも操作末尾まで有限バッファに置く。
全登録connectionのcloseを確認できなければ未保存分を破棄する。接続前の開始も
ハング・クラッシュ時には失われ得る。発生日付ごとにまとめて書き、DB接続中の
ファイルI/Oやイベントごとのignore subprocessを追加しない。

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
当日にも末尾日付を付け、遅延イベントは発生時現地日付へ追記する。日付が戻れば
以前の日へ追記する。空の日のファイル、日付なしcurrent、renameはない。
thread lockと専用OS artifact lockをこの順で取得し、非待機1回で競合を断念する。
lockファイルの存在は所有の証拠にしない。再確認・容量検査・追記・closeまでを囲む。
logger.lockは共有artifact lockのrequire_single_linkを有効にし、最初の書込前と
取得後に開いたdescriptorとパスのリンク数1を確認する。他の利用者の既定契約は変えない。
os.writeの非バッファ追記で、不完全な末尾行はLFで分離し切り詰めない。
読み手は不正行を不完全記録として扱う。電源断時の永続性は保証しない。
1日10 MiB、最大30日付ファイル、合計100 MiB。厳密な名前のファイルだけを数え、
ディレクトリ走査自体は128エントリまで。容量超過・検査不能なら保存せず継続する。
自動削除・上書き・ローテーションはない。利用者の明示整理まで総量上限は戻らない。
ログはcoreバックアップ、配布物、source commitから除外する。

<a id="implementation-boundaries"></a>

## 実装境界

valuesは記録前投影、debug_logは相関・span・有限バッファ・SQLite adapter、sinkは
固定パスadmissionと日別追記を所有する。業務エラー、transaction cleanup、Task入力検査の
所有者は変更しない。ログのためのDB接続・migration・ネットワーク・背景処理を追加しない。

<a id="verification"></a>

## 検証

短い呼出し例、OFF/ON/errors、数値と状態、ネスト秘密と未知オブジェクト、開始終了と
例外不変、実SQLite失敗・close、日跨ぎと遅延、thread/process競合、途中行と容量・
書込失敗、マーカー変更とunsafe path、公開出力とdoctor/read-onlyを隔離検証する。
OFF/ONの全体時間と出力量を比較し、文書・配布整合を確認する。レビューはTier 2。
