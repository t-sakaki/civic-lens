"""シチュエーション別テンプレート

市民が「何を請求していいかわからない」問題を解決するため、
典型的なシチュエーションごとの必要文書テンプレートを事前定義する。

これはGeminiに推測させるのではなく、行政の実務で実際に開示されている
文書のリストを市民に提示する。
"""
from typing import Dict


SITUATION_TEMPLATES = {
    "overseas_trip": {
        "key": "overseas_trip",
        "label": "海外視察・出張",
        "emoji": "✈️",
        "description": "市長・議員・職員の海外視察・出張に関する文書",
        "documents": [
            "出張命令書",
            "復命書（出張報告書）",
            "行程表・訪問先一覧",
            "面談記録・議事録",
            "会計伝票・領収書",
            "随行者リスト",
            "成果報告資料",
        ],
        "ordinance_ground": "第7条第4号（事務執行影響）",
        "sample_anger_text": "市長・議員の海外視察費用が高すぎる。何百万円も使って成果が分からないのは許せない。",
        "counter_argument_hint": "「意思決定後の情報については開示すべき時期に来ている」（最判平11.12.16）",
    },
    "public_works": {
        "key": "public_works",
        "label": "公共事業・工事",
        "emoji": "🏗️",
        "description": "公共工事の入札・契約・施工に関する文書",
        "documents": [
            "契約書",
            "設計書・仕様書",
            "積算根拠・内訳書",
            "変更契約書",
            "検査調書・完了検査記録",
            "入札参加者一覧",
            "低入札価格調査資料",
        ],
        "ordinance_ground": "第7条第4号",
        "sample_anger_text": "公共工事の設計変更が繰り返されている。不当に高い見積もりでは？",
        "counter_argument_hint": "契約金額・相手方は原則開示。単価・積算根拠は競争上の地位を理由に一部非開示の場合あり",
    },
    "subsidy": {
        "key": "subsidy",
        "label": "補助金・交付金",
        "emoji": "💰",
        "description": "補助金の交付・運用に関する文書",
        "documents": [
            "交付申請書",
            "交付決定書",
            "交付額算定根拠",
            "実績報告書",
            "会計書類・領収書",
            "監査結果報告書",
            "効果測定資料",
        ],
        "ordinance_ground": "第7条第4号",
        "sample_anger_text": "補助金交付先が適切か検証したい。使途が不明。",
        "counter_argument_hint": "補助金の交付先・金額は原則開示",
    },
    "police_complaint": {
        "key": "police_complaint",
        "label": "警察への「たらい回し」検証",
        "emoji": "📞",
        "description": "警察に相談したのに相手にされなかった事案の検証記録",
        "documents": [
            "110番通報受付・対応記録（日時・内容）",
            "警察安全相談の受付記録",
            "相談事案の受理・不受理判断記録",
            "たらい回し状況の統計データ",
            "相談者対応の経過記録",
            "他機関への転送記録",
        ],
        "ordinance_ground": "第5条第5号",
        "sample_anger_text": "警察に相談したが、たらい回しにされた。本当に受理されたか確認したい。",
        "counter_argument_hint": "相談受付記録・統計情報は開示対象。個別事案の対応経過は記録されているはず",
    },
    "police_discipline": {
        "key": "police_discipline",
        "label": "警察官の懲戒処分",
        "emoji": "⚠️",
        "description": "警察官の懲戒処分・服務規律違反記録",
        "documents": [
            "懲戒処分書",
            "事実認定書",
            "弁明書",
            "処分理由書",
        ],
        "ordinance_ground": "第5条第1号（個人特定除く）",
        "sample_anger_text": "不祥事を起こした警察官が誰か知りたい。処分が軽いのでは？",
        "counter_argument_hint": "個人を特定しない形式での統計・処分件数は開示可能",
    },
    "police_stop": {
        "key": "police_stop",
        "label": "職務質問（職質）",
        "emoji": "🚶",
        "description": "職務質問の実施状況・統計",
        "documents": [
            "職務質問実施統計",
            "時間帯別実施状況",
            "地域別実施状況",
            "事案別実施件数",
        ],
        "ordinance_ground": "第5条第5号",
        "sample_anger_text": "職質を頻繁に行われている気がする。統計データを確認したい。",
        "counter_argument_hint": "統計情報・実施件数は開示可能。個別事案は捜査情報で非公開",
    },
    "lost_found": {
        "key": "lost_found",
        "label": "遺失物・拾得物",
        "emoji": "💼",
        "description": "遺失物・拾得物の処理記録",
        "documents": [
            "拾得物件処理簿",
            "遺失届受理記録",
            "保管期間満了記録",
            "処理件数統計",
        ],
        "ordinance_ground": "第5条第5号",
        "sample_anger_text": "落とした財布が警察でどう処理されたか知りたい。",
        "counter_argument_hint": "拾得物の処理手順・統計情報は開示可能",
    },
    "koban": {
        "key": "koban",
        "label": "交番・警察署運営",
        "emoji": "🏛️",
        "description": "交番・警察署の運営・契約・予算",
        "documents": [
            "庁舎管理委託契約書",
            "物品購入契約記録",
            "予算執行記録",
            "備品管理台帳",
        ],
        "ordinance_ground": "第5条第5号",
        "sample_anger_text": "交番が閉鎖されるらしいが、根拠資料を見たい。",
        "counter_argument_hint": "行政運営・契約情報は通常の行政情報と同じ取扱い",
    },
    "traffic": {
        "key": "traffic",
        "label": "交通違反取締統計",
        "emoji": "🚦",
        "description": "交通違反取締りの統計情報",
        "documents": [
            "違反種別取締件数",
            "地域別・時間帯別統計",
            "飲酒運転取締状況",
            "速度違反取締統計",
        ],
        "ordinance_ground": "第5条第5号",
        "sample_anger_text": "この地区で速度違反の取り締まりが偏ってないか？",
        "counter_argument_hint": "統計情報は公益性が高く開示対象",
    },
    "police_safety": {
        "key": "police_safety",
        "label": "警察安全相談（DV・ストーカー等）",
        "emoji": "🛡️",
        "description": "DV・ストーカー等の警察安全相談の受付・対応記録",
        "documents": [
            "相談受付件数統計",
            "相談種別内訳",
            "対応結果統計",
            "匿名集計データ",
            "他機関連携記録",
            "相談対応の標準フロー文書",
        ],
        "ordinance_ground": "第5条第5号",
        "sample_anger_text": "DVの相談が警察でどう扱われているか知りたい。たらい回しにされていないか。",
        "counter_argument_hint": "統計・処理件数は開示可能。個別相談は個人情報だが、統計パターンから傾向は読み取れる",
    },
    "environment": {
        "key": "environment",
        "label": "環境・測定データ",
        "emoji": "🌱",
        "description": "環境アセスメント・大気測定・公害関連データ",
        "documents": [
            "環境影響評価書",
            "大気・水質測定データ",
            "監視カメラ記録",
            "公害苦情処理記録",
            "事業者への指導文書",
        ],
        "ordinance_ground": "第7条第4号",
        "sample_anger_text": "工場から変な臭いがする。測定データを確認したい。",
        "counter_argument_hint": "環境データは公益性が高く、部分開示・加工開示が認められる",
    },
    "contract": {
        "key": "contract",
        "label": "委託・業務委託",
        "emoji": "📋",
        "description": "外部委託・業務委託に関する文書",
        "documents": [
            "委託契約書",
            "仕様書・業務範囲",
            "再委託先一覧",
            "完了報告書",
            "成果物・納品物",
        ],
        "ordinance_ground": "第7条第4号",
        "sample_anger_text": "なぜこの業者に委託したのか不明。随意契約が多すぎないか。",
        "counter_argument_hint": "委託先・金額は原則開示。選定理由は部分開示",
    },
    "regulation": {
        "key": "regulation",
        "label": "条例・規則改正",
        "emoji": "📜",
        "description": "条例・規則の改正過程・議事録",
        "documents": [
            "条例改正案の審議記録",
            "パブリックコメント結果",
            "関係団体への意見聴取記録",
            "新旧対照表",
        ],
        "ordinance_ground": "第7条第3号（審議検討情報）",
        "sample_anger_text": "新しい条例がなぜ作られたのか、市民意見がどう反映されたか知りたい。",
        "counter_argument_hint": "意思決定後の情報は開示すべき",
    },
    "disaster": {
        "key": "disaster",
        "label": "災害・防災",
        "emoji": "🚨",
        "description": "災害対応・防災計画に関する文書",
        "documents": [
            "防災計画書",
            "避難指示発令記録",
            "罹災証明発行記録",
            "支援物資配布記録",
            "災害対応時系列記録",
        ],
        "ordinance_ground": "第7条第4号",
        "sample_anger_text": "避難指示が出なかったのはなぜ？市の対応に疑問。",
        "counter_argument_hint": "災害対応記録は公益性が高く、原則として開示",
    },
    "court_admin": {
        "key": "court_admin",
        "label": "裁判所司法行政・事務処理要領",
        "emoji": "⚖️",
        "description": "裁判所の執務提要・通達・裁判官会議録・統計データ",
        "documents": [
            "事務処理要領・執務提要（司法行政文書）",
            "最高裁判所通達および執務連絡文書",
            "裁判官会議の議事録・要旨",
            "委員会（庁舎管理・事件管理等）の配付資料・議事概要",
            "統計資料・事件処理状況の集計データ",
        ],
        "ordinance_ground": "第4条第4号（裁判所事務処理影響）",
        "sample_anger_text": "裁判所の事務処理手順や運用の内規がブラックボックスになっている。司法行政文書の開示を求めたい。",
        "counter_argument_hint": "個別事件の訴訟記録ではなく組織的運用基準や通達等の司法行政文書は開示対象。事務遂行への支障は具体的立証が必要",
    },
    "court_budget_facility": {
        "key": "court_budget_facility",
        "label": "裁判所の予算執行・施設整備",
        "emoji": "🏛️",
        "description": "裁判所庁舎の修繕・入札契約・システム調達・旅費精算",
        "documents": [
            "庁舎改修・修繕工事請負契約書・設計仕様書",
            "ITシステム調達・備品購入の入札結果表および契約書",
            "職員・裁判官の出張伺い・旅費精算伝票一式",
            "公費支出・予算執行に関する伺書・決裁録",
        ],
        "ordinance_ground": "第4条第4号",
        "sample_anger_text": "裁判所の施設改修や調度品購入、公費の使途が不透明。契約書や精算書を確認したい。",
        "counter_argument_hint": "司法機関であっても会計・契約・公金支出に関する文書は高い透明性が求められ、原則開示の対象",
    },
}


# シチュエーションごとに、対象機関を絞り込むためのカテゴリ（自治体・警察・裁判所）。
# STEP 0 でシチュエーションを選んだ時点で、請求先の性質は決まっているため、
# 🏛️ 対象機関 の選択肢をこのカテゴリで絞り込む。
SITUATION_CATEGORY = {
    "overseas_trip": "自治体",
    "public_works": "自治体",
    "subsidy": "自治体",
    "environment": "自治体",
    "contract": "自治体",
    "regulation": "自治体",
    "disaster": "自治体",
    "police_complaint": "警察",
    "police_discipline": "警察",
    "police_stop": "警察",
    "lost_found": "警察",
    "koban": "警察",
    "traffic": "警察",
    "police_safety": "警察",
    "court_admin": "裁判所",
    "court_budget_facility": "裁判所",
}


def get_situation_list():
    """シチュエーション一覧を取得（UI表示用）"""
    return [
        {
            "key": s["key"],
            "label": s["label"],
            "emoji": s["emoji"],
            "description": s["description"],
            "category": SITUATION_CATEGORY.get(s["key"]),
        }
        for s in SITUATION_TEMPLATES.values()
    ]


def get_situation(key: str):
    """シチュエーションを取得"""
    situation = SITUATION_TEMPLATES.get(key)
    if situation is None:
        return None
    return {**situation, "category": SITUATION_CATEGORY.get(key)}


def get_situation_by_label(label: str):
    """ラベルからシチュエーションを取得"""
    for s in SITUATION_TEMPLATES.values():
        if s["label"] == label:
            return s
    return None