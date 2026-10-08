# 音声パネル 発話同期＋表情変化（案B+C）実装計画

> **For Claude Code:** この計画をタスクごとに実装すること。各タスクは2〜5分の作業。テスト先は `AUTH_SECRET_KEY` 環境変数が必要（未設定だとコレクションエラー）なので、テスト実行コマンドは下記をそのまま使うこと。

**Goal:** 音声パネル再生時に、発話中のSDGsエージェントをアイコン点灯＋表情変化で可視化し、話者が交代したことが一目でわかるUIにする。

**Architecture:** 純フロントエンド実装。`voice_panel.py` の台本構成（免責1行 → 怒りの強い順に最大3エージェント → 統合エージェント）と350ms行間無音を既知の前提として、ブラウザ側 `audio.currentTime` を台本の開始時刻に照らして発話中話者を判定する。バックエンド変更なし（台本の時間情報が欲しくなったらTask 5で拡張できる設計に留める）。

**Tech Stack:** Vanilla JS（既存 `templates/index.html` の記述スタイル踏襲）、CSS `@keyframes`（既存 `agentBlink`/`agentLineIn` と同居）、Tailwind CDN。新規依存ライブラリなし。

**最上位原則（必ず守ること）:**
- 顔・キャラは「一目でAIとわかる」デザインに限定する。実写風・人間そのものの顔は作らない（なりすまし抑制）
- SDGsの色・アイコンは公式17色を参照するが、公式ロゴの改変・再配布はしない（色参照と簡易アイコン自作のみ）
- 音声冒頭の免責読み上げ（`voice_panel.OPENING`）は削らない。免責中は全エージェントに「AI生成」であることがわかる状態（グレーアウト等）を見せる
- 既存機能（ガードレール表示、開示請求提案、リアクション）に触れない

**既知の前提（コードリーディング済み）:**
- 台本構成: `voice_panel.py:47` `build_script()` → 先頭が免責（ナレーション）、続いて `voices` を `anger_level` 降順に最大 `MAX_AGENT_LINES=3` 体、末尾に統合エージェント（`summary` があるときのみ）
- UI: `templates/index.html:3786` `renderNewsSynthesis()` に音声パネル実装（3782 `shortAgentLabel`、3816 ボタン、3819 `<audio>`、3830 クリックハンドラ）
- `voices` の各要素は `{theme, label, remark, anger_level, pseudo_citizen_voice, guardrail}` を持つ（`news_anger_agent.py:229`）。**現在はAPI応答に台本の時間情報は含まれない**
- SDGsラベル: `目標N …` 形式（`news_anger_agent.py:80-90`）、UIでは `shortAgentLabel` で `SDGN …` に変換済み
- 既存アニメ: `@keyframes agentLineIn` / `agentBlink`（index.html:221-236）

**テスト実行コマンド（毎回これを使う）:**
```bash
cd /home/taira/civic-lens && AUTH_SECRET_KEY=test-key-for-dev-only .venv/bin/python -m pytest tests/test_guardrail_cost_voice.py -q
```
期待値: `17 passed`（Task 1完了後はこの数が増える）

---

### Task 1: 台本の時間情報をAPI応答に含める（バックエンド）

**Objective:** フロントエンドが `audio.currentTime` と同期するために、各話者の開始・終了推定時刻を応答JSONに含める。

**Files:**
- Modify: `voice_panel.py`（`build_script` と `synthesize_panel`）
- Test: `tests/test_guardrail_cost_voice.py`

**Step 1: 失敗するテストを書く**

`tests/test_guardrail_cost_voice.py` に追加:

```python
def test_panel_timing_has_speaker_ranges(monkeypatch):
    monkeypatch.setattr(voice_panel, "_synthesize_line", lambda c, v, t: b"\x01\x00" * 100)
    wav = voice_panel.synthesize_panel(object(), "n9", RECORD)
    timing = voice_panel.panel_timing(RECORD, wav)
    assert timing[0]["speaker"] == "ナレーション"
    assert timing[0]["start"] == 0.0
    # 各行は start < end、かつ350msギャップで行が進む
    for a, b in zip(timing, timing[1:]):
        assert b["start"] >= a["end"]
    assert len(timing) == len(voice_panel.build_script(RECORD))
```

**Step 2: テストを実行して失敗を確認**

```bash
cd /home/taira/civic-lens && AUTH_SECRET_KEY=test-key-for-dev-only .venv/bin/python -m pytest tests/test_guardrail_cost_voice.py::test_panel_timing_has_speaker_ranges -q
```
期待: FAIL — `panel_timing` が存在しない

**Step 3: 最小実装**

`voice_panel.py` に追加（フロントと同じ推定ロジックを単一の真実源にするため、バックエンド側で計算して返す）:

```python
def panel_timing(record: dict[str, Any], wav: bytes) -> list[dict[str, Any]]:
    """台本の各話者の開始・終了時刻（秒）を推定して返す。

    synthesize_panel は1行ずつ合成して350msギャップで連結するため、
    実WAVのフレーム長から話者ごとの推定区間を算出する。
    """
    script = build_script(record)
    gap_s = 0.35
    with wave.open(io.BytesIO(wav)) as w:
        total = w.getnframes() / w.getframerate()
    # 実WAVの長さから各行の音声長を比例配分する（350msギャップは行間のみ）
    n = len(script)
    if n == 0 or total <= gap_s * (n - 1):
        # 極端に短いWAVは話者0のみでフォールバック
        return [{"speaker": script[0]["speaker"] if script else "?", "start": 0.0, "end": total}]
    speech_total = total - gap_s * (n - 1)
    out = []
    cursor = 0.0
    for i, line in enumerate(script):
        speech = speech_total / n  # 比例配分（行長の正確な差異は許容）
        out.append({"speaker": line["speaker"], "start": round(cursor, 2), "end": round(cursor + speech, 2)})
        cursor += speech + gap_s
    return out
```

`app.py` の `news_voice_panel`（897行目付近）は現状WAVバイナリをそのまま返している。**JSONに時間を混ぜると既存のblob再生を壊すため、応答形式は変えない。** 代わりに、時間情報はフロント側で同じ比例配分を再計算する（Task 2）。したがってこのTask 1の `panel_timing` は **テストで仕様を固定する単一の真実源** として残す（フロントJSは同じアルゴリズムを実装する）。ボーナスとして `GET /api/news-agent/voice-panel-timing?news_id=` を追加してもよいが必須ではない（YAGNI: 追加しない）。

**Step 4: テストを実行して合格を確認**

```bash
cd /home/taira/civic-lens && AUTH_SECRET_KEY=test-key-for-dev-only .venv/bin/python -m pytest tests/test_guardrail_cost_voice.py -q
```
期待: `18 passed`

**Step 5: コミット**

```bash
git add voice_panel.py tests/test_guardrail_cost_voice.py
git commit -m "feat: 音声パネルの話者別時間推定を追加"
```

---

### Task 2: 発話同期UI — 話者アイコン行とcurrentTime同期（案C）

**Objective:** 音声パネル再生中、発話中の話者アイコンを点灯させる。SDGs公式17色を参照した簡易アイコン（円形バッジ＋「SDGN」短縮ラベル。ロゴ再配布はしない）。

**Files:**
- Modify: `templates/index.html`（`renderNewsSynthesis` 内、3815-3850付近）

**Step 1: SDGs色マップとアイコン行の追加**

`shortAgentLabel`（3782）の下に追加:

```javascript
        const SDG_COLORS = {
            1: '#E5243B', 2: '#DDA63A', 3: '#4C9F38', 4: '#C5192D', 5: '#FF3A21',
            6: '#26BDE2', 7: '#FCC30B', 8: '#A21942', 9: '#FD6925', 10: '#DD1367',
            11: '#FD9D24', 12: '#BF8B2E', 13: '#3F7E44', 14: '#0A97D9', 15: '#56C02B',
            16: '#00689D', 17: '#19486A',
        };
        const agentIcon = (label) => {
            const m = String(label || '').match(/^目標(\d+)/);
            const n = m ? Number(m[1]) : 0;
            const color = SDG_COLORS[n] || '#64748b'; // general/不明はグレー
            const short = m ? `SDG${n}` : '監視';
            return `<span class="vp-agent-icon" data-sdg="${n}" style="--sdg-color:${color}">${short}</span>`;
        };
```

**Step 2: 台本の時間推定（フロント側）**

Task 1と同じアルゴリズムをJSに実装（`renderNewsSynthesis` のクリックハンドラ内、WAV取得成功後に実行）:

```javascript
        const buildPanelTiming = (record, audioEl) => {
            // voice_panel.build_script と同じ構成: 免責1 + 怒り降順3 + 統合
            const lines = [{ speaker: 'ナレーション' }];
            const sorted = [...voices].sort((a, b) => (b.anger_level || 0) - (a.anger_level || 0)).slice(0, 3);
            sorted.forEach(v => {
                if (!v.pseudo_citizen_voice && !v.remark) return;
                lines.push({ speaker: `AI・${shortAgentLabel(v.label)}`, label: v.label });
            });
            if (record.summary) lines.push({ speaker: 'AI・統合エージェント' });
            const gap = 0.35;
            const dur = audioEl.duration || 0;
            const n = lines.length;
            if (n === 0 || dur <= gap * (n - 1)) return [];
            const speech = (dur - gap * (n - 1)) / n;
            let cursor = 0;
            return lines.map(l => {
                const t = { ...l, start: cursor, end: cursor + speech };
                cursor += speech + gap;
                return t;
            });
        };
```

**注意（整合性）:** `voice_panel.build_script` は「`pseudo_citizen_voice` も `remark` も空の行は飛ばす」（voice_panel.py:52-54）。上のJSも同じ条件でスキップしている。ただし ** voicesの並び順とスキップ条件が後で変わったら両方を直すこと（この共有はTask 1の `panel_timing` のdocstringにも追記済みの前提）。

**Step 3: パネルUIのHTML変更**

3816-3819を、ボタンの下にアイコン行を追加した形に置換:

```html
                    <button type="button" class="news-voice-panel-btn text-xs border px-2.5 py-1.5 rounded-md hover:bg-gray-50">🔊 エージェントたちの議論を音声で聴く</button>
                    <span class="text-xs text-gray-400">…（既存のガードレール表示、変更なし）…</span>
                </div>
                <div class="news-voice-panel-agents flex items-center gap-2 flex-wrap mb-2 hidden"></div>
                <audio class="news-voice-panel-audio w-full mb-2 hidden" controls></audio>
```

**Step 4: 同期ハンドラの追加**

既存クリックハンドラ（3830-3850）の `panelAudio.play()` 成功後と `panelAudio` の `timeupdate` に同期を追加:

```javascript
            const agentsRow = bubble.querySelector('.news-voice-panel-agents');
            let panelTiming = [];
            const highlightSpeaker = () => {
                if (!panelTiming.length) return;
                const t = panelTiming.find(x => audio.currentTime >= x.start && audio.currentTime < x.end);
                agentsRow.querySelectorAll('.vp-agent-icon').forEach(el => {
                    const active = t && (el.dataset.speaker === t.speaker);
                    el.classList.toggle('vp-speaking', !!active);
                });
                agentsRow.querySelectorAll('.vp-agent-disclaimer').forEach(el => {
                    el.classList.toggle('hidden', !!(t && t.speaker !== 'ナレーション'));
                });
            };
            audio.addEventListener('timeupdate', highlightSpeaker);
```

アイコン行はWAV取得成功時に構築（免責中＝全員グレー＋「AI生成」バッジ表示）:

```javascript
                    // panelAudio.src 設定後:
                    panelTiming = buildPanelTiming(record, panelAudio);
                    agentsRow.innerHTML = panelTiming.filter(x => x.speaker !== 'ナレーション').map(x =>
                        `<span data-speaker="${escapeHtml(x.speaker)}">${agentIcon(x.label || '')}<span class="text-xs text-gray-500">${escapeHtml(x.speaker)}</span></span>`
                    ).join('') + `<span class="vp-agent-disclaimer text-xs text-gray-400">🤖 AI生成の議論（フィクション）</span>`;
                    agentsRow.classList.remove('hidden');
```

**Step 5: 手動確認**

```bash
cd /home/taira/civic-lens && .venv/bin/uvicorn app:app --port 8095
```
ブラウザでニュース分析 → 「🔊 エージェントたちの議論を音声で聴く」→ 免責中は全員グレー、話者が変わるとアイコンが点灯することを確認。

**Step 6: コミット**

```bash
git add templates/index.html
git commit -m "feat: 音声パネルに話者アイコンと発話同期を実装"
```

---

### Task 3: 表情変化 — 怒りレベル連動の簡易表情（案B）

**Objective:** アイコンに怒りレベル連動の3段階表情（CSSのみ。画像・YouCam不要）を持たせ、発話中は該当エージェントの表情が動く。

**Files:**
- Modify: `templates/index.html`（CSS追加と `agentIcon` の拡張）

**Step 1: 表情CSSの追加**

既存 `agentBlink`（234）の下に追加:

```css
        /* --- 音声パネル: SDGsエージェントの簡易表情（怒りレベル3段階・AIと分かる意匠） --- */
        .vp-agent-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            border: 2px solid var(--sdg-color, #64748b);
            color: var(--sdg-color, #64748b);
            background: #fff;
            font-size: 11px;
            font-weight: 700;
            opacity: 0.55;
            transition: opacity 0.25s, transform 0.25s, box-shadow 0.25s;
        }
        .vp-agent-icon.vp-speaking {
            opacity: 1;
            transform: scale(1.15);
            box-shadow: 0 0 0 3px color-mix(in srgb, var(--sdg-color) 30%, transparent);
            animation: vpPulse 1s ease-in-out infinite;
        }
        @keyframes vpPulse {
            50% { transform: scale(1.15) translateY(-1px); }
        }
        /* 怒りレベル連動: 高=濃色+震え / 中=通常 / 低=薄色 */
        .vp-anger-high { border-width: 3px; }
        .vp-speaking.vp-anger-high { animation: vpPulse 0.7s ease-in-out infinite, vpShake 0.15s linear infinite; }
        .vp-anger-low { opacity: 0.35; }
        @keyframes vpShake {
            0%, 100% { margin-left: 0; } 50% { margin-left: 1px; }
        }
```

**Step 2: 怒りレベルクラスの付与**

Task 2の `agentsRow.innerHTML` 構築部分で、`voices` から該当テーマの `anger_level` を引いて付与:

```javascript
                    agentsRow.innerHTML = panelTiming.filter(x => x.speaker !== 'ナレーション').map(x => {
                        const v = voices.find(w => x.speaker.includes(shortAgentLabel(w.label)));
                        const lvl = Number(v?.anger_level || 0);
                        const angerCls = lvl >= 7 ? 'vp-anger-high' : (lvl > 0 && lvl <= 3 ? 'vp-anger-low' : '');
                        return `<span data-speaker="${escapeHtml(x.speaker)}" class="${angerCls}">${agentIcon(x.label || '')}<span class="text-xs text-gray-500">${escapeHtml(x.speaker)}</span></span>`;
                    }).join('') + `<span class="vp-agent-disclaimer text-xs text-gray-400">🤖 AI生成の議論（フィクション）</span>`;
```

**注意:** 怒りレベルの閾値（7/3）は暫定。`.vp-anger-high` に「怒」を想起させる震えを持たせているが、**人間の顔の表情は作らない**（AIアイコンのまま震え・明滅で表現）。実写風の顔・実在人物風キャラはこの計画のスコープ外。

**Step 3: 手動確認**

音声再生で、怒りレベル7以上のエージェントのアイコンだけ震えが速く、レベル3以下は薄く表示されることを確認。

**Step 4: コミット**

```bash
git add templates/index.html
git commit -m "feat: 怒りレベル連動のエージェント表情を追加"
```

---

### Task 4: モバイル・最終検証

**Objective:** タッチ操作（モバイルSafari/Chrome Android）と全テスト・全フローの最終確認。

**Files:**
- 修正なし（検証のみ。問題が出たら修正してからコミット）

**Step 1: 全テスト**

```bash
cd /home/taira/civic-lens && AUTH_SECRET_KEY=test-key-for-dev-only .venv/bin/python -m pytest tests/test_guardrail_cost_voice.py -q
```
期待: `18 passed`

**Step 2: 手動確認リスト**

- [ ] デスクトップChrome: 音声再生中にアイコンが話者順に点灯する
- [ ] 免責読み上げ中は全員グレー＋「AI生成の議論」バッジが見える
- [ ] 怒りレベル連動の表情（震え/通常/薄色）が3段階で見分けられる
- [ ] モバイル（iOS Safari / Chrome Android）: タップで音声再生→同様に同期する
- [ ] `prefers-reduced-motion` 環境でも崩れない（震えは控えめでも可視状態は維持）
- [ ] 音声なし（503）時はアイコン行が表示されない（テキスト表示のまま）
- [ ] 履歴から再表示したときも同じ動作になる

**Step 3: 問題があれば修正してコミット**

```bash
git add -A && git commit -m "fix: 音声パネル同期のモバイル・アクセシビリティ対応"
```

---

## スコープ外（明示）

- **YouCam API（表情解析）**: 本件の用途に合わないため使わない（AGENTS.mdのスポンサー技術表のとおり任意）
- **17体分のキャラクターデザイン**: なりすましリスクと工数の問題で見送り。SDGs色参照の簡易アイコンで代替
- **口パクの本格アニメ**: アイコン明滅＋震えで代替
- **`vite`/ビルド導入**: 既存の単一HTML + Tailwind CDN構成を維持

## 提出物への関係

- 提出締切は **2026年10月15日（木）23:59**。この計画は1〜2日で完了できる分量
- 審査軸「自律性・エージェントらしさ」: 名乗り出→発話→表情の流れが視覚化され、デモ動画の見せ場になる
- デモ動画（3分以内、YouTube公開）: Task 4の手動確認シーンをそのまま録画に使える
