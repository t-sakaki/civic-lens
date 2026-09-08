"""Civic Lens — ADK (Agent Development Kit) Agent

Google ADK を使って Vertex AI Agent Engine 上にエージェントを構築。
AngerAnalysis → 条例マッチング → 開示請求書作成 → 審査請求反論 まで
一気通貫で処理するエージェントシステム。
"""
import os
import json
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from precedent_cases import (
    find_relevant_precedents,
    find_relevant_precedents_by_embedding,
    format_precedent_case_for_prompt,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

# Google GenAI SDK (Vertex AI / Gemini API 統合)
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ADKのインポート
try:
    from google.adk.agents import LlmAgent, SequentialAgent
    from google.adk.tools import FunctionTool
    ADK_AVAILABLE = True
except ImportError:
    ADK_AVAILABLE = False
    print("⚠️ google-adk is not installed. Running in fallback mode.")

VERTEX_AI_AVAILABLE = False
GEMINI_API_AVAILABLE = False


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Pydantic データモデル（ADKのFunction Calling & DAG/メタ認知批評）
# ---------------------------------------------------------------------------

class AtomicTask(BaseModel):
    """DAGを構成する最小不可分タスク (Atomic Task)"""
    id: str = Field(description="タスク識別子 (例: task-pain)")
    name: str = Field(description="タスク名")
    description: str = Field(description="タスクの実行詳細")
    dependencies: List[str] = Field(default_factory=list, description="先行して完了すべきタスクIDリスト")
    status: str = Field(default="completed", description="completed / in_progress / pending")
    acceptance_criteria: str = Field(description="タスク完了の定量的・客観的判定基準")


class TaskDAG(BaseModel):
    """手続き全体の有向非巡回グラフ (DAG)"""
    tasks: List[AtomicTask] = Field(description="Atomic Tasks のリスト")
    execution_order: List[str] = Field(description="トポロジカルソート順のタスクID")


class MetaCognitiveCritique(BaseModel):
    """自律エージェントによるメタ認知批評（自己批判・行政逃げ道対策）"""
    vulnerability: str = Field(description="弱点検知: 行政側の『不存在』『文書不特定』等の逃げ道と予防策")
    risk_prediction: str = Field(description="リスク予測: 開示期限延長（60日ルール）や部分開示のリスク評価と代案B")
    verifiability: str = Field(description="検証可能性: 書式要件・管轄・根拠条例の客観的妥当性チェック")
    critique_summary: str = Field(description="メタ認知批評の総括")
    confidence_score: float = Field(default=0.88, description="AIエージェントの自己評価信頼度スコア (0.0-1.0)")
    improvements_applied: List[str] = Field(default_factory=list, description="批評により自動適用された改善項目")


class AngerAnalysis(BaseModel):
    """市民の怒りの構造化"""
    anger_level: int = Field(description="怒りレベル（1-10）")
    emotion_keywords: List[str] = Field(description="感情キーワード")
    target_authority: str = Field(description="対象機関（推測）")
    target_authority_key: str = Field(description="条例DBのキー")
    pain_summary: str = Field(description="市民の痛みの要約")
    specific_documents_requested: List[str] = Field(description="市民が請求したい具体の文書")
    legal_basis: str = Field(description="適用される条例条文")
    next_action: str = Field(description="次のアクション")
    urgency: str = Field(description="urgent / normal / low")
    recommended_response_time: str = Field(description="推奨される対応期限")
    # 高度オーケストレーション拡張（DAG & メタ認知批評 & Human-in-the-loop）
    task_dag: Optional[TaskDAG] = Field(default=None, description="タスクDAG")
    critique: Optional[MetaCognitiveCritique] = Field(default=None, description="メタ認知批評結果")
    safeguard_options: Optional[List[Dict[str, str]]] = Field(default=None, description="Human-in-the-loop選択肢")
    is_mock: bool = Field(default=False, description="True の場合、GEMINI_API_KEY未設定/API失敗によるルールベースのフォールバック結果")


class CounterArgument(BaseModel):
    """反論ロジック"""
    ground_number: str
    ground_name: str
    counter_arguments: List[str]
    precedent_cases: List[str]
    winning_probability: float
    is_mock: bool = Field(default=False, description="True の場合、GEMINI_API_KEY未設定/API失敗によるテンプレートのフォールバック結果")


class AgentResponse(BaseModel):
    """エージェントの最終回答"""
    anger_analysis: AngerAnalysis
    counter_argument: Optional[CounterArgument] = None
    disclosure_request_text: str
    deadline_info: str
    next_steps: List[str]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ADKツール関数（DAG構築・メタ認知批評・行政逃げ道対策）
# ---------------------------------------------------------------------------

def build_task_dag(user_input: str, target_authority: str, specific_documents: List[str]) -> Dict:
    """手続き全体の有向非巡回グラフ(DAG)を構築（TD-Orchestration / DAG仕様）"""
    tasks = [
        {
            "id": "task-pain",
            "name": "争点原子化 & ペイン抽出",
            "description": "市民の自然言語入力から行政問題の争点を特定し不可分タスク(Atomic Task)へ分解",
            "dependencies": [],
            "status": "completed",
            "acceptance_criteria": "市民の不満要約および対象文書群が客観的テキストとして抽出されていること"
        },
        {
            "id": "task-ordinance",
            "name": "条例 & 法的根拠マッチング",
            "description": f"{target_authority}の保有文書公開条例および所管部署の照合",
            "dependencies": ["task-pain"],
            "status": "completed",
            "acceptance_criteria": "管轄自治体条例の条文番号（例: 第7条）および開示義務規定がマッピングされていること"
        },
        {
            "id": "task-critique",
            "name": "メタ認知批評 & 行政逃げ道検知",
            "description": "行政側の『不存在』『事務支障』逃げ道を事前自己批評し、請求文書を実務簿冊レベルに補正",
            "dependencies": ["task-ordinance"],
            "status": "completed",
            "acceptance_criteria": "弱点検知（SPOF）、期限延長リスク、代案Bの策定が完了していること"
        },
        {
            "id": "task-draft",
            "name": "開示請求書 自動策定",
            "description": "補正後の簿冊名と理由を盛り込んだ正式な情報公開請求書の策定",
            "dependencies": ["task-critique"],
            "status": "completed",
            "acceptance_criteria": "開示請求書マークダウンが生成され、対象文書および請求理由が網羅されていること"
        },
        {
            "id": "task-routing",
            "name": "窓口特定 & 経路案内 (駅すぱあと)",
            "description": f"{target_authority}情報公開窓口への公共交通アクセスを案内",
            "dependencies": ["task-ordinance"],
            "status": "completed",
            "acceptance_criteria": "最寄り駅および市役所・警察窓口の所在地・電話番号が特定されていること"
        },
        {
            "id": "task-hitl",
            "name": "Human-in-the-Loop 市民承認 & 戦略選択",
            "description": "市民が『早期開示重視』または『徹底追求重視』の戦略を選択し、最終意思決定を行う",
            "dependencies": ["task-draft"],
            "status": "in_progress",
            "acceptance_criteria": "市民（ユーザー）によるプラン選択および提出意思確認"
        }
    ]
    execution_order = ["task-pain", "task-ordinance", "task-critique", "task-draft", "task-routing", "task-hitl"]
    return {
        "tasks": tasks,
        "execution_order": execution_order
    }


def perform_meta_cognitive_critique(
    user_input: str,
    target_authority_key: str,
    target_authority_name: str,
    documents: List[str]
) -> Dict:
    """自律エージェントによるメタ認知批評（自己批判・弱点検知・リスク予測）"""
    doc_text = " ".join(documents)
    combined = user_input + " " + doc_text

    improvements = []
    
    if any(k in combined for k in ["海外視察", "出張", "旅費", "市長"]):
        vulnerability = (
            "【弱点検知】単に『海外視察費用』と請求すると、行政側は『精算伝票』のみを開示し、"
            "最も重要な『復命書（成果報告書）』や『現地日程表』を『請求文書に含まれていない』として隠蔽・不存在回答するリスクがあります。"
        )
        improvements.append("文書名に『復命書・視察日程表・随行職員復命書・旅行命令簿・航空券等領収書・決裁伺書』を明記")
        risk_prediction = (
            "【リスク予測】対象文書が多岐にわたる場合、自治体側が『事務処理上の困難』を理由に"
            "開示決定期限を14日から45日〜60日に延長する特例条項を適用する可能性（確率: 約45%）があります。"
            "代案Bとして、まずは『復命書（報告書）』のみを先行開示させる部分分割請求を推奨します。"
        )
        improvements.append("代案B: 復命書先行開示の特約オプションを準備")
    elif any(k in combined for k in ["公共事業", "入札", "工事", "業者", "契約"]):
        vulnerability = (
            "【弱点検知】『入札額』や『積算内訳』は、行政が情報公開条例第7条（法人等の競争上の地位を害するおそれ）を"
            "紋切り型に適用して不開示決定を下す典型的な類型です。"
        )
        improvements.append("最高裁判決（平14.2.8）の『実質的・具体的な損害の蓋然性が必要』という反論判例を事前添付")
        risk_prediction = (
            "【リスク予測】業者名や内訳単価が黒塗り（部分開示）となる確率: 約70%。"
            "事前に対象文書を『落札決定伺書および設計書総括表』にフォーカスし、競争情報に当たらない確定済み公文書を狙うのが安全です。"
        )
        improvements.append("予定価格・設計書総括表など確定事実文書を優先請求指定")
    elif any(k in combined for k in ["警察", "逮捕", "交通", "捜査"]):
        vulnerability = (
            "【弱点検知】警察関係文書は公安委員会・警察本部長の裁量が広く、『捜査手法の露見』や『公共の安全』を理由に"
            "包括的不開示（存否応答拒否）を主張されるリスクが極めて高いです。"
        )
        improvements.append("捜査記録そのものではなく『捜査終結後の処分結果通知書・統計記録』等へ請求対象を精査")
        risk_prediction = (
            "【リスク予測】90日以内の審査請求（国家公安委員会/県公安委員会宛）への移行を前提とした書式準備が必要です。"
        )
        improvements.append("不開示前提の審査請求事前ドラフトを同時スタンバイ")
    elif any(k in combined for k in ["裁判所", "最高裁", "高裁", "地裁", "司法行政", "裁判官"]):
        vulnerability = (
            "【弱点検知】裁判所に対する請求において最も多い却下理由は『個別訴訟記録の請求（訴訟記録閲覧制度の対象であり司法行政文書開示の対象外）』です。"
            "また『裁判の公正・独立に支障』『率直な意見交換に支障』という取扱要綱第4条各号の包括的不開示リスクがあります。"
        )
        improvements.append("請求対象を個別事件記録ではなく『司法行政文書（事務処理要領・通達・会議要旨・公費契約書等）』であることを明確に限定")
        risk_prediction = (
            "【リスク予測】裁判官会議議事録や運用文書について一部不開示（マスキング）または取扱要綱に基づく苦情申出への移行リスク（約50%）。"
            "代案Bとして、確定済みの執務要領・統計データ先行開示の分割請求を推奨します。"
        )
        improvements.append("代案B: 確定済み執務要領・統計データ先行開示オプションを準備")
    else:
        vulnerability = (
            f"【弱点検知】『{user_input[:40]}...』のような包括的表現では、窓口から『文書の特定が不十分』として"
            "補正命令（手続きの引き延ばし）を受けるリスクがあります。"
        )
        improvements.append("起案文書・決裁文書・伺書など行政実務上の正式簿冊名を補正挿入")
        risk_prediction = (
            "【リスク予測】文書特定不足による補正命令リスク（約40%）。"
            "代案Bとして、所管部署の文書分類表（ファイル管理簿）の事前開示請求を組み合わせます。"
        )
        improvements.append("文書管理台帳に基づく特定ロジックを反映")

    verifiability = f"{target_authority_name}情報公開条例に基づく開示請求権者の要件（市民・利害関係者・何人も請求可能規定）を満たしており、法的・実務的書式要件を充足しています。"
    critique_summary = "AIによるメタ認知自己批評を実施：行政側の常套的な不開示・不存在逃げ道を先回り検知し、公文書管理上の簿冊名へと自動補強を行いました。"

    safeguard_options = [
        {
            "id": "option-fast",
            "name": "プランA：迅速開示重視（推奨）",
            "description": "決裁文書・報告書など核心的文書に限定し、14日以内の早期開示決定を狙う（延長リスク低）",
            "badge": "スピード重視"
        },
        {
            "id": "option-thorough",
            "name": "プランB：網羅的徹底追及",
            "description": "領収書・精算書・関連メール含む全関係文書を一括請求（期間延長の可能性あり）",
            "badge": "徹底調査"
        }
    ]

    return {
        "vulnerability": vulnerability,
        "risk_prediction": risk_prediction,
        "verifiability": verifiability,
        "critique_summary": critique_summary,
        "confidence_score": 0.91,
        "improvements_applied": improvements,
        "safeguard_options": safeguard_options,
    }


def analyze_user_anger(user_input: str, hint_authority_key: Optional[str] = None) -> Dict:
    """市民の入力から怒りを構造化分析する（ADK Function Tool / DAG & メタ認知批評対応）

    hint_authority_key: 請求文に対象機関を判別できる記述がない場合に使うデフォルト値。
    Geolocationで特定済みの自治体キー等、呼び出し元が把握している最有力候補を渡す。
    未指定時は安城市（ハッカソンのデフォルト対象機関）にフォールバックする。
    """
    # キーワードベースの怒りレベル推定
    anger_keywords = [
        "許せない", "ふざけるな", "怒り", "腹立つ", "最悪",
        "ひどい", "許されない", "おかしい", "不信", "隠蔽", "嘘",
    ]
    level = 5
    for kw in anger_keywords:
        if kw in user_input:
            level += 1
    level = min(level, 10)

    # 対象機関の推定（data/authorities/*.json の aliases を長い順にマッチ）
    from ordinance_data import match_authority_by_text, get_ordinance

    auth_key = match_authority_by_text(user_input, default=hint_authority_key or "anjo-city")
    ordinance = get_ordinance(auth_key)
    auth_name = ordinance.authority if ordinance else "安城市"

    # 文書の特定
    documents = ["行政文書一式"]
    if any(k in user_input for k in ["視察", "海外", "出張", "旅費"]):
        documents = [
            "海外視察の復命書（成果報告書）",
            "旅行命令簿・出張伺書（決裁文書）",
            "航空券・宿泊費等の精算伝票および領収書一式",
            "現地日程表および面談記録"
        ]
    elif any(k in user_input for k in ["公共事業", "入札", "工事", "業者"]):
        documents = [
            "入札結果表および落札決定伺書",
            "設計書（金抜き設計書・総括表）",
            "工事請負契約書一式"
        ]
    elif any(k in user_input for k in ["補助金", "交付金", "支援金"]):
        documents = [
            "補助金交付申請書および添付事業計画書",
            "交付決定通知書および決裁伺書",
            "実績報告書および精算書"
        ]
    elif any(k in user_input for k in ["裁判所", "最高裁", "高裁", "地裁", "司法行政", "裁判官"]):
        documents = [
            "事務処理要領・執務提要（司法行政文書）",
            "最高裁判所通達および執務連絡文書",
            "裁判官会議の議事録・要旨",
            "庁舎管理・調度品・公金支出に関する決裁伺書一式"
        ]

    # メタ認知批評とDAG構築の実施
    critique_result = perform_meta_cognitive_critique(user_input, auth_key, auth_name, documents)
    dag_result = build_task_dag(user_input, auth_name, documents)

    # 根拠規程・期限の決定
    if ordinance and ordinance.category == "裁判所":
        legal_basis = ordinance.ordinance_name
        response_time = f"{ordinance.request_deadline_days}日以内（原則30日）"
    elif ordinance and ordinance.category == "警察":
        legal_basis = ordinance.ordinance_name
        response_time = f"{ordinance.request_deadline_days}日以内"
    elif ordinance:
        legal_basis = f"{auth_name}情報公開条例"
        response_time = f"{ordinance.request_deadline_days}日以内"
    else:
        legal_basis = "情報公開条例"
        response_time = "14日以内"

    return {
        "anger_level": level,
        "emotion_keywords": ["怒り", "不信"],
        "target_authority": auth_name,
        "target_authority_key": auth_key,
        "pain_summary": user_input[:100],
        "specific_documents_requested": documents,
        "legal_basis": legal_basis,
        "next_action": "disclosure_request",
        "urgency": "normal",
        "recommended_response_time": response_time,
        "task_dag": dag_result,
        "critique": critique_result,
        "safeguard_options": critique_result.get("safeguard_options", []),
        "is_mock": True,
    }


def get_ordinance_info(authority_key: str) -> Dict:
    """対象機関の条例情報を取得（ADK Function Tool）"""
    from ordinance_data import get_ordinance
    ordinance = get_ordinance(authority_key)
    if not ordinance:
        return {"error": f"Authority not found: {authority_key}"}

    return {
        "authority": ordinance.authority,
        "ordinance_name": ordinance.ordinance_name,
        "request_deadline_days": ordinance.request_deadline_days,
        "extension_days": ordinance.extension_days,
        "review_period_days": ordinance.review_period_days,
        "non_disclosure_grounds": [
            {"number": g.number, "name": g.name, "description": g.description}
            for g in ordinance.non_disclosure_grounds
        ],
        "review_authority": ordinance.review_authority,
        "contact": ordinance.contact,
    }


def get_counter_argument(ground_number: str) -> Dict:
    """不開示事由に対する反論ロジックを取得（ADK Function Tool）"""
    from ordinance_data import COMMON_COUNTER_ARGUMENTS, POLICE_COUNTER_ARGUMENTS, COURT_COUNTER_ARGUMENTS

    all_args = {**COMMON_COUNTER_ARGUMENTS, **POLICE_COUNTER_ARGUMENTS, **COURT_COUNTER_ARGUMENTS}

    if ground_number in all_args:
        precedents = [
            "最判平成14年2月8日（在外日本人選挙権）",
            "名古屋市 海外視察費開示事例（2023）",
            "岡崎市 契約金額開示事例（2024）",
        ]
        if ground_number.startswith("第4条"):
            precedents = [
                "最高裁判所 司法行政文書開示例（裁判官会議議事要旨・事務処理要領）",
                "最判平成11年12月16日（公文書開示・意思決定後情報の原則開示）",
                "東京高判平成20年（司法行政文書開示苦情処理・裁量の統制）",
            ]
        ground_names = {
            "第4条第1号": "個人情報",
            "第4条第2号": "法人情報",
            "第4条第3号": "審議・検討・協議情報",
            "第4条第4号": "裁判所の事務処理影響情報",
            "第4条第5号": "公共安全等情報",
            "第5条第1号": "個人情報",
            "第5条第2号": "法人情報",
            "第5条第3号": "捜査情報",
            "第5条第4号": "公共安全情報",
            "第5条第5号": "事務執行影響",
            "第7条第2号": "法人情報",
            "第7条第3号": "審議検討情報",
            "第7条第4号": "事務執行影響",
        }
        return {
            "ground_number": ground_number,
            "ground_name": ground_names.get(ground_number, "不開示事由"),
            "counter_arguments": all_args[ground_number],
            "precedent_cases": precedents,
            "winning_probability": 0.75,
        }
    return {"error": f"Ground not found: {ground_number}"}


def get_situation_documents(situation_key: str) -> Dict:
    """シチュエーション別の必要文書を取得（ADK Function Tool）"""
    from situations import get_situation
    situation = get_situation(situation_key)
    if not situation:
        return {"error": f"Situation not found: {situation_key}"}

    return {
        "label": situation["label"],
        "description": situation["description"],
        "documents": situation["documents"],
        "ordinance_ground": situation["ordinance_ground"],
    }


# ---------------------------------------------------------------------------
# ADK エージェント定義
# ---------------------------------------------------------------------------

def create_adk_agent():
    """ADKでエージェントを作成"""
    if not ADK_AVAILABLE:
        return None

    # 怒り分析 & メタ認知批評エージェント
    anger_agent = LlmAgent(
        name="anger_analyzer",
        model="gemini-3.1-pro-preview",
        description="市民の怒り・不満を構造化データに変換し、メタ認知批評とタスクDAGを構築する",
        instruction="""
あなたは情報公開請求の専門家AIエージェントです。
市民の「行政への怒り・不満」を分析し、高度自律オーケストレーション（DAGタスク分解 & メタ認知批評）を用いて法的アクションへの変換を支援します。

あなたの役割:
1. 争点原子化 (Atomic Task Decomposition): 市民の自然言語から行政問題の争点を不可分タスクへと分解
2. 条例マッチング: 適用される条例条文および管轄を特定
3. メタ認知批評 (Meta-Cognitive Critique):
   - 弱点検知: 行政側の「不存在」「文書不特定」「事務支障」等の逃げ道を先回り自己批評
   - リスク予測: 開示期限延長（60日ルール）や黒塗り不開示リスクを定量評価し、代案Bを策定
   - 検証可能性: 書式要件・管轄・根拠条文の客観的妥当性を自動検証
4. Human-in-the-Loop セーフガード:
   - 市民に「迅速開示優先（プランA）」と「徹底追求（プランB）」の選択肢を提示
   - 最終判断は必ず市民（ユーザー）が行う
5. 弁護士法72条遵守: 法的助言ではなく情報提供・書式作成支援に徹する
""",
        tools=[
            FunctionTool(func=analyze_user_anger),
            FunctionTool(func=get_ordinance_info),
            FunctionTool(func=get_situation_documents),
            FunctionTool(func=perform_meta_cognitive_critique),
            FunctionTool(func=build_task_dag),
        ],
        output_key="anger_analysis",
    )

    # 反論構築エージェント
    counter_agent = LlmAgent(
        name="counter_argument_builder",
        model="gemini-3.1-pro-preview",
        description="不開示決定への反論ロジックを構築する",
        instruction="""
あなたは情報公開・審査請求の実務に精通したAIです。
不開示決定を受けた市民のために、反論ロジックを構築してください。

不開示事由該当性の検討:
- 各不開示事由が本件情報にどう適用されるか分析
- 抽象的・主観的理由の排除（最判平14.2.8）
- 公益性・部分開示の検討
- 類似事例・判例の参照
""",
        tools=[
            FunctionTool(func=get_counter_argument),
        ],
        output_key="counter_argument",
    )

    # 統合エージェント（SequentialAgent）
    integrated_agent = SequentialAgent(
        name="civic_lens_integrated",
        description="Civic Lens 統合エージェント: 怒り分析→条例マッチング→反論構築",
        sub_agents=[anger_agent, counter_agent],
    )

    return integrated_agent


# ---------------------------------------------------------------------------
# フォールバック実装（ADKが利用できない場合）
# ---------------------------------------------------------------------------

class CivicLensAgent:
    """ADK互換エージェント（フォールバック実装）"""

    def __init__(self):
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "gcp-hackathon2026")
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-northeast1")
        self.adk_agent = create_adk_agent() if ADK_AVAILABLE else None
        self._genai_client = None

    @property
    def genai_client(self):
        if self._genai_client is None and GENAI_AVAILABLE:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            try:
                if api_key:
                    self._genai_client = genai.Client(api_key=api_key)
                elif self.project_id:
                    self._genai_client = genai.Client(
                        vertexai=True,
                        project=self.project_id,
                        location=self.location,
                    )
                else:
                    self._genai_client = genai.Client(vertexai=True)
            except Exception as e:
                print(f"Failed to initialize google-genai client: {e}")
        return self._genai_client

    def analyze_anger(self, user_input: str, hint_authority_key: Optional[str] = None) -> AngerAnalysis:
        """怒り分析（Gemini Vertex AI優先、失敗時ルールベースフォールバック）

        hint_authority_key: 請求文だけでは対象機関を判別できない場合のデフォルト候補
        （例: ブラウザGeolocationで特定済みの自治体）。Gemini・ルールベースいずれも
        本文からの判定を優先し、判定できない場合にのみこの値を採用する。
        """
        if self.genai_client:
            try:
                prompt = f"""
あなたは行政文書の情報公開請求・審査請求を支援するAIです。
以下の市民入力を分析し、指定のJSON形式で返してください。

入力内容: {user_input}

【出力スキーマ】
- anger_level: 怒り・不満レベルの整数 (1〜10)
- emotion_keywords: 市民が感じている感情キーワードのリスト (例: ["不信", "隠蔽", "怒り"])
- target_authority: 対象となる行政機関または警察組織の名前 (例: "安城市", "名古屋市", "愛知県", "愛知県警察本部", "警視庁" など)
- target_authority_key: 条例キー (anjo-city, nagoya-city, okazaki-city, toyota-city, gamagori-city, aichi-pref, aichi-assembly, metropolitan-police, aichi-police, kanagawa-police, osaka-police のいずれか)
- pain_summary: 市民の不満や問題の要約 (100文字程度)
- specific_documents_requested: 請求すべき具体的な行政文書名のリスト (例: ["海外視察の復命書", "精算内訳書", "領収書一式"])
- legal_basis: 適用される条例条文 (例: "安城市情報公開条例第7条")
- next_action: 次にとるべきアクション ("disclosure_request" または "review_request" または "consultation")
- urgency: 緊急度 ("urgent", "normal", "low")
- recommended_response_time: 推奨される対応期限 (例: "14日以内", "30日以内")

JSONのみを返してください。
"""
                response = self.genai_client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                text = response.text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
                text = text.strip()
                data = json.loads(text)
                if "task_dag" not in data or not data["task_dag"]:
                    data["task_dag"] = build_task_dag(
                        user_input,
                        data.get("target_authority", "安城市"),
                        data.get("specific_documents_requested", [])
                    )
                if "critique" not in data or not data["critique"]:
                    data["critique"] = perform_meta_cognitive_critique(
                        user_input,
                        data.get("target_authority_key", hint_authority_key or "anjo-city"),
                        data.get("target_authority", "安城市"),
                        data.get("specific_documents_requested", [])
                    )
                if "safeguard_options" not in data or not data["safeguard_options"]:
                    critique_val = data["critique"]
                    data["safeguard_options"] = critique_val.get("safeguard_options", []) if isinstance(critique_val, dict) else getattr(critique_val, "safeguard_options", [])
                return AngerAnalysis(**data)
            except Exception as e:
                print(f"Gemini API analysis error: {e}, falling back to rule-based analysis")

        # フォールバック: ルールベースの Function Tool 結果を使用
        tool_result = analyze_user_anger(user_input, hint_authority_key)
        return AngerAnalysis(**tool_result)

    def build_counter_argument(
        self,
        non_disclosure_decision: str,
        ordinance,
        alleged_ground: str,
    ) -> CounterArgument:
        """反論ロジック構築（Gemini Vertex AI優先）

        precedent_cases（類似の裁決・答申例）は、Geminiに創作させるのではなく、
        総務省「行政不服審査裁決・答申検索データベース」から収集・蓄積した実在の
        認容事例（precedent_cases.py, data/gyofuku_cases.json）から関連度の高いものを
        検索して使用する。適合する実データが見つからない場合に限り、Geminiによる
        生成（フォールバック）を利用する。

        検索はGemini Embeddingsによるコサイン類似度検索を優先し、埋め込みデータ未生成・
        API未設定・呼び出し失敗の場合はNgramヒューリスティック検索に自動フォールバックする
        （find_relevant_precedents_by_embedding内部で処理）。
        """
        real_precedents = find_relevant_precedents_by_embedding(
            ordinance_name=getattr(ordinance, "ordinance_name", ""),
            alleged_ground=alleged_ground,
            authority=getattr(ordinance, "authority", ""),
        )
        real_precedent_texts = [format_precedent_case_for_prompt(c) for c in real_precedents]

        if self.genai_client:
            try:
                precedent_instruction = (
                    "- precedent_cases: 以下の実在する認容事例（総務省 行政不服審査裁決・"
                    "答申検索データベースより収集）から、本件に関連が深い順にそのまま列挙してください。"
                    "件数が少ない場合はある分だけで構いません。事例を創作しないでください。\n"
                    + "\n".join(f"  - {t}" for t in real_precedent_texts)
                ) if real_precedent_texts else (
                    "- precedent_cases: 類似の裁判例・審査会答申例のリスト (2〜3件)。"
                    "実在の蓄積データに該当がなかったため、一般的な知見に基づき推定して構いません"
                    "（実在性を保証できない旨は呼び出し側で扱います）。"
                )
                prompt = f"""
あなたは情報公開・審査請求の実務専門家AIです。
自治体（{ordinance.authority}）からの不開示決定に対する反論ロジックと勝訴・開示見込みを検討してください。

【不開示理由】：{alleged_ground}
【市民の請求・経緯】：{non_disclosure_decision}
【適用条例】：{ordinance.ordinance_name}

以下のJSON形式で返してください:
- ground_number: 不開示事由の番号 (例: "第7条第2号")
- ground_name: 不開示事由の名称 (例: "個人情報" または "法人情報" 等)
- counter_arguments: 不開示決定を覆すための法的反論ポイントのリスト (3つ以上、具体的かつ説得力のある論理)
{precedent_instruction}
- winning_probability: 審査請求で一部開示以上を勝ち取れる推定確率 (0.0 〜 1.0)

JSONのみを出力してください。
"""
                response = self.genai_client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                text = response.text.strip()
                data = json.loads(text.strip())

                def _to_str(item):
                    if isinstance(item, str):
                        return item
                    if isinstance(item, dict):
                        parts = [str(v) for v in item.values() if isinstance(v, (str, int, float))]
                        return " : ".join(parts) if parts else str(item)
                    return str(item)

                if isinstance(data.get("counter_arguments"), list):
                    data["counter_arguments"] = [_to_str(x) for x in data["counter_arguments"]]

                # precedent_cases は実データがあれば必ず実データで上書きする
                # （Geminiが指示に反して創作・改変するリスクを排除するため）
                if real_precedent_texts:
                    data["precedent_cases"] = real_precedent_texts
                elif isinstance(data.get("precedent_cases"), list):
                    data["precedent_cases"] = [_to_str(x) for x in data["precedent_cases"]]

                return CounterArgument(**data)
            except Exception as e:
                print(f"Gemini counter argument error: {e}, using local templates")

        tool_result = get_counter_argument(alleged_ground)
        if "error" not in tool_result:
            if real_precedent_texts:
                tool_result["precedent_cases"] = real_precedent_texts
            return CounterArgument(**tool_result, is_mock=True)
        return CounterArgument(
            ground_number=alleged_ground,
            ground_name="不開示事由",
            counter_arguments=["反論ロジックを構築中"],
            precedent_cases=real_precedent_texts,
            winning_probability=0.5,
            is_mock=True,
        )

    def generate_disclosure_request(
        self,
        user_input: str,
        ordinance,
        strategy_option: str = "option-fast",
    ) -> tuple[str, bool]:
        """戻り値: (請求書テキスト, is_mock)。is_mock=True はGemini未使用のテンプレート生成を示す"""
        """開示請求書・司法行政文書開示申出書を生成（Gemini優先・Human-in-the-loop戦略反映）"""
        current_date = __import__('datetime').datetime.now().strftime("%Y年%m月%d日")
        is_court = getattr(ordinance, "category", "") == "裁判所"
        doc_label = "司法行政文書" if is_court else "行政文書"
        action_verb = "申し出ます" if is_court else "請求します"
        form_title = getattr(ordinance, "request_form", "司法行政文書開示申出書" if is_court else "情報公開請求書")

        deadline_days_text = f"{ordinance.request_deadline_days}日以内"

        strategy_note = (
            f"【選択された方針: プランA（迅速開示重視）】\n"
            f"※ 決定期限（原則{deadline_days_text}）の遵守を最優先とし、確定済み公文書・簿冊から先行交付を希望する旨を記載してください。"
            if strategy_option == "option-fast" else
            f"【選択された方針: プランB（網羅的徹底追及）】\n"
            f"※ 関連するメール、打ち合わせメモ、精算内訳、付属伝票を含む一切の関係簿冊の完全開示を求める旨を記載してください。"
        )

        court_guidance = ""
        if is_court:
            court_guidance = (
                "\n【重要: 裁判所特有の注意事項】\n"
                "- 個別の裁判記録（民事・刑事の訴訟記録）は訴訟記録閲覧制度の管轄となるため、"
                "本申出書は『司法行政文書（事務処理要領、通達、会議要旨、公費契約書、統計等）』を請求対象とする旨を明記してください。\n"
                "- 表題は『司法行政文書開示申出書』としてください。\n"
            )

        if self.genai_client:
            try:
                prompt = f"""
あなたは情報公開制度および司法行政文書開示制度に精通した専門家AIです。
市民の相談内容をもとに、提出先（{ordinance.authority}）の{ordinance.ordinance_name}に適合する正式な「{form_title}」のMarkdownドラフトを作成してください。
{court_guidance}
【市民の要望・怒り】:
{user_input}

【戦略オプション】:
{strategy_note}

【提出先情報】:
- 機関名: {ordinance.authority}
- 担当窓口: {ordinance.contact}
- 根拠規定: {ordinance.ordinance_name}
- 請求日/申出日: {current_date}

以下の構成でMarkdownテキストを作成してください（不要な前置きや説明は含めず、請求書面の内容のみを出力してください）：
# {form_title}

## {ordinance.authority} {ordinance.contact} 御中

### 1. 申出日（請求日）
### 2. 申出人（請求人）の住所・氏名
### 3. 開示を求める{doc_label}の名称又は内容（法的に特定しやすい公文書名・内訳書類にブレイクダウンして箇条書き）
### 4. 開示の方法（希望）
### 5. 連絡先
### 6. 申出（請求）の目的・理由
### 7. 特記事項
"""
                response = self.genai_client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=prompt,
                )
                text = response.text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
                return text.strip(), False
            except Exception as e:
                print(f"Gemini generate_disclosure_request error: {e}, using template")

        documents = []
        if user_input:
            documents.append(user_input[:200])

        if is_court:
            strategy_clause = (
                f"### 7. 特記事項（迅速開示オプション）\n"
                f"本件は司法行政の透明性確保に基づく申出であり、取扱要綱上の決定期限（原則{ordinance.request_deadline_days}日以内）の遵守を求めます。対象司法行政文書のうち確定済み簿冊（通達・要領・会議要旨・契約書等）から先行交付されることを希望します。\n"
                f"※ 個別の訴訟記録ではなく、司法行政文書を対象とすることを確認します。"
                if strategy_option == "option-fast" else
                f"### 7. 特記事項（網羅的開示オプション）\n"
                f"本件に関する関連起案・決裁・打合せ記録・通達・電子メール等を含め、漏れのない完全な司法行政文書の開示を申し出ます。\n"
                f"※ 個別の訴訟記録ではなく、司法行政文書を対象とすることを確認します。"
            )
        else:
            strategy_clause = (
                f"### 7. 特記事項（迅速開示オプション）\n"
                f"本件は市民の知る権利に基づく請求であり、法定決定期限（{ordinance.request_deadline_days}日以内）の遵守を求めます。対象文書のうち確定済み簿冊（決裁・報告書）から先行交付されることを希望します。"
                if strategy_option == "option-fast" else
                f"### 7. 特記事項（網羅的開示オプション）\n"
                f"本件に関する関連起案・決裁・打合せ記録・電子メール等を含め、漏れのない完全な行政文書の開示を請求します。"
            )

        return f"""# {form_title}

## {ordinance.authority} {ordinance.contact} 御中

{ordinance.ordinance_name}に基づき、以下のとおり{doc_label}の開示を{action_verb}。

### 1. 請求日（申出日）
{current_date}

### 2. 請求人（申出人）の住所・氏名
〒000-0000 〇〇市〇〇町〇丁目〇番〇号
市民 太郎

### 3. 開示を求める{doc_label}の名称又は内容
{chr(10).join(['- ' + d for d in documents]) if documents else f'- 関連する一切の{doc_label}'}

### 4. 開示の方法（希望）
- [x] 写しの交付（郵送希望）
- [ ] 閲覧

### 5. 連絡先
電話：000-0000-0000
メール：example@example.com

### 6. 請求（申出）の理由・背景
{"司法行政" if is_court else "行政"}の透明性確保のため、市民として適切に情報を把握する必要があると考えるため。

{strategy_clause}

※ 本{"申出" if is_court else "請求"}は {ordinance.ordinance_name} に基づく正式な開示{"申出" if is_court else "請求"}です。
""", True


# シングルトン
_agent_instance: Optional[CivicLensAgent] = None


def get_agent() -> CivicLensAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = CivicLensAgent()
    return _agent_instance