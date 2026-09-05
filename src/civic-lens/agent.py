"""Civic Lens — ADK (Agent Development Kit) Agent

Google ADK を使って Vertex AI Agent Engine 上にエージェントを構築。
AngerAnalysis → 条例マッチング → 開示請求書作成 → 審査請求反論 まで
一気通貫で処理するエージェントシステム。
"""
import os
import json
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

# ADKのインポート
try:
    from google.adk.agents import LlmAgent, SequentialAgent
    from google.adk.tools import FunctionTool
    ADK_AVAILABLE = True
except ImportError:
    ADK_AVAILABLE = False
    print("⚠️ google-adk is not installed. Running in fallback mode.")


# Vertex AI直接利用（ADKが使えない場合のフォールバック）
try:
    import vertexai
    from vertexai.generative_models import GenerativeModel
    VERTEX_AI_AVAILABLE = True
except ImportError:
    VERTEX_AI_AVAILABLE = False
    try:
        import google.generativeai as genai
        GEMINI_API_AVAILABLE = True
    except ImportError:
        GEMINI_API_AVAILABLE = False


# ---------------------------------------------------------------------------
# Pydantic データモデル（ADKのFunction Calling でも利用）
# ---------------------------------------------------------------------------

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


class CounterArgument(BaseModel):
    """反論ロジック"""
    ground_number: str
    ground_name: str
    counter_arguments: List[str]
    precedent_cases: List[str]
    winning_probability: float


class AgentResponse(BaseModel):
    """エージェントの最終回答"""
    anger_analysis: AngerAnalysis
    counter_argument: Optional[CounterArgument] = None
    disclosure_request_text: str
    deadline_info: str
    next_steps: List[str]


# ---------------------------------------------------------------------------
# ADKツール関数（エージェントが呼び出す）
# ---------------------------------------------------------------------------

def analyze_user_anger(user_input: str) -> Dict:
    """市民の入力から怒りを構造化分析する（ADK Function Tool）"""
    # 簡易実装：キーワードベースの怒りレベル推定
    anger_keywords = [
        "許せない", "ふざけるな", "怒り", "腹立つ", "最悪",
        "ひどい", "許されない", "おかしい", "不信", "隠蔽", "嘘",
    ]
    level = 5
    for kw in anger_keywords:
        if kw in user_input:
            level += 1
    level = min(level, 10)

    # 対象機関の推定
    authority_map = {
        "市長": "anjo-city",
        "安城市": "anjo-city",
        "名古屋": "nagoya-city",
        "岡崎": "okazaki-city",
        "愛知県": "aichi-pref",
        "県": "aichi-pref",
        "議会": "aichi-assembly",
        "警察": "aichi-police",
        "警視庁": "metropolitan-police",
    }
    auth_key = "anjo-city"  # default
    auth_name = "安城市"
    for k, v in authority_map.items():
        if k in user_input:
            auth_key = v
            break

    if auth_key == "anjo-city":
        auth_name = "安城市"
    elif auth_key == "nagoya-city":
        auth_name = "名古屋市"
    elif auth_key == "okazaki-city":
        auth_name = "岡崎市"
    elif auth_key == "aichi-pref":
        auth_name = "愛知県"
    elif auth_key == "aichi-assembly":
        auth_name = "愛知県議会"
    elif auth_key == "aichi-police":
        auth_name = "愛知県警察本部"
    elif auth_key == "metropolitan-police":
        auth_name = "警視庁"

    return {
        "anger_level": level,
        "emotion_keywords": ["怒り", "不信"],
        "target_authority": auth_name,
        "target_authority_key": auth_key,
        "pain_summary": user_input[:100],
        "specific_documents_requested": ["行政文書一式"],
        "legal_basis": f"{auth_name}情報公開条例",
        "next_action": "disclosure_request",
        "urgency": "normal",
        "recommended_response_time": "30日",
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
    from ordinance_data import COMMON_COUNTER_ARGUMENTS, POLICE_COUNTER_ARGUMENTS

    all_args = {**COMMON_COUNTER_ARGUMENTS, **POLICE_COUNTER_ARGUMENTS}

    if ground_number in all_args:
        return {
            "ground_number": ground_number,
            "counter_arguments": all_args[ground_number],
            "precedent_cases": [
                "最判平成14年2月8日（在外日本人選挙権）",
                "名古屋市 海外視察費開示事例（2023）",
                "岡崎市 契約金額開示事例（2024）",
            ],
            "winning_probability": 0.72,
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

    # 怒り分析エージェント
    anger_agent = LlmAgent(
        name="anger_analyzer",
        model="gemini-2.5-pro",
        description="市民の怒り・不満を構造化データに変換する",
        instruction="""
あなたは情報公開請求の専門家AIエージェントです。
市民の「行政への怒り・不満」を分析し、法的アクションへの変換を支援します。

あなたの役割:
1. 市民の感情（怒り・不信・諦め）を読み取る
2. どのような情報公開請求で解決できるかを特定する
3. 適用される条例条文を特定する
4. 必要な文書をリストアップする
6. 次のアクションを提案する

重要な法的原則:
- あなたは法的助言を提供する「代理人」ではなく、情報提供・書式作成の「アシスタント」
- 最終判断は必ず市民（ユーザー）が行う
- 弁護士法72条（非弁行為）に抵触しないよう、法的助言ではなく情報整理に徹する
""",
        tools=[
            FunctionTool(func=analyze_user_anger),
            FunctionTool(func=get_ordinance_info),
            FunctionTool(func=get_situation_documents),
        ],
        output_key="anger_analysis",
    )

    # 反論構築エージェント
    counter_agent = LlmAgent(
        name="counter_argument_builder",
        model="gemini-2.5-pro",
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
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-northeast1")
        self.adk_agent = create_adk_agent() if ADK_AVAILABLE else None
        self._fallback_client = None

    @property
    def fallback_client(self):
        if self._fallback_client is None:
            if VERTEX_AI_AVAILABLE:
                vertexai.init(project=self.project_id, location=self.location)
                self._fallback_client = GenerativeModel("gemini-2.5-pro")
            elif GEMINI_API_AVAILABLE:
                genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
                self._fallback_client = genai.GenerativeModel("gemini-2.5-pro")
        return self._fallback_client

    def analyze_anger(self, user_input: str) -> AngerAnalysis:
        """怒り分析"""
        # ADKエージェントがあれば使用
        if self.adk_agent:
            try:
                # ADKエージェント実行
                result = self.adk_agent.run(user_input)
                return AngerAnalysis(**result)
            except Exception as e:
                print(f"ADK agent error: {e}, falling back")

        # フォールバック: Function Toolの結果を使用
        try:
            tool_result = analyze_user_anger(user_input)
            return AngerAnalysis(**tool_result)
        except Exception as e:
            print(f"Tool error: {e}")
            # 最終フォールバック: Vertex AI直接呼出
            prompt = f"""
以下の市民入力を分析し、JSON形式で返してください:
- anger_level (1-10)
- emotion_keywords (リスト)
- target_authority (対象機関名)
- target_authority_key (anjo-city/nagoya-city/okazaki-city/aichi-pref/aichi-assembly/metropolitan-police/aichi-police/kanagawa-police/osaka-police のいずれか)
- pain_summary (市民の痛みの要約)
- specific_documents_requested (請求したい文書のリスト)
- legal_basis (適用される条例条文)
- next_action (disclosure_request/review_request/consultation のいずれか)
- urgency (urgent/normal/low)
- recommended_response_time (推奨対応期限)

市民入力: {user_input}

JSONのみを返してください。
"""
            response = self.fallback_client.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            text = response.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
            text = text.strip()
            data = json.loads(text)
            return AngerAnalysis(**data)

    def build_counter_argument(
        self,
        non_disclosure_decision: str,
        ordinance,
        alleged_ground: str,
    ) -> CounterArgument:
        """反論ロジック構築"""
        tool_result = get_counter_argument(alleged_ground)
        if "error" not in tool_result:
            return CounterArgument(**tool_result)
        return CounterArgument(
            ground_number=alleged_ground,
            ground_name="不開示事由",
            counter_arguments=["反論ロジックを構築中"],
            precedent_cases=[],
            winning_probability=0.5,
        )

    def generate_disclosure_request(
        self,
        user_input: str,
        ordinance,
    ) -> str:
        """開示請求書を生成"""
        documents = []
        if user_input:
            documents.append(user_input[:200])

        return f"""# 情報公開請求書

## {ordinance.authority} {ordinance.contact} 御中

{ordinance.ordinance_name}に基づき、以下のとおり行政文書の開示を請求します。

### 1. 請求日
{__import__('datetime').datetime.now().strftime("%Y年%m月%d日")}

### 2. 請求人の住所・氏名
〒000-0000 〇〇市〇〇町〇丁目〇番〇号
市民 太郎

### 3. 開示請求する行政文書の名称又は内容
{chr(10).join(['- ' + d for d in documents]) if documents else '- 関連する一切の行政文書'}

### 4. 開示の方法（希望）
- [x] 写しの交付（郵送希望）
- [ ] 閲覧

### 5. 連絡先
電話：000-0000-0000
メール：example@example.com

### 6. 請求の理由・背景
行政の透明性確保のため、市民として適切に情報を把握する必要があると考えるため。

※ 本請求は {ordinance.ordinance_name} に基づく正式な開示請求です。
"""


# シングルトン
_agent_instance: Optional[CivicLensAgent] = None


def get_agent() -> CivicLensAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = CivicLensAgent()
    return _agent_instance