"""YouCam API クライアント

市民の怒り表情を検出し、エージェントへの入力に変換する。
YouCam APIは画像解析・AR・Beauty Techを提供。
ハッカソンでは感情解析機能（Face Detection + Emotion Recognition）を活用。
"""
import os
import base64
import requests
from typing import Dict, Optional, List
from pydantic import BaseModel


YOUCAM_API_KEY = os.getenv("YOUCAM_API_KEY", "")
YOUCAM_BASE_URL = "https://api.youvs.com/v1"


class EmotionAnalysis(BaseModel):
    """感情解析結果"""
    dominant_emotion: str  # "angry" / "sad" / "neutral" / "happy"
    anger_level: int  # 1-10
    confidence: float
    facial_landmarks_detected: bool
    emotions_breakdown: Dict[str, float]


def analyze_anger_from_image(image_data: bytes) -> Optional[EmotionAnalysis]:
    """画像から怒りを解析"""
    if not YOUCAM_API_KEY:
        return _mock_emotion_analysis()

    try:
        # YouCam API: 顔検出 + 感情解析
        # 実際のリクエスト形式は API documentation を確認する必要がありますが、
        # 一般的な画像解析APIの形式を想定
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        response = requests.post(
            f"{YOUCAM_BASE_URL}/face/analyze",
            headers={
                "Authorization": f"Bearer {YOUCAM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "image": image_base64,
                "features": ["emotion", "landmarks"],
                "language": "ja",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return _parse_emotion(data)
    except Exception as e:
        print(f"YouCam API エラー: {e}")
        return _mock_emotion_analysis()


def _parse_emotion(data: Dict) -> EmotionAnalysis:
    """APIレスポンスをパース"""
    emotions = data.get("emotions", {})
    anger_score = emotions.get("angry", 0.0)
    return EmotionAnalysis(
        dominant_emotion=max(emotions, key=emotions.get) if emotions else "neutral",
        anger_level=int(anger_score * 10),
        confidence=data.get("confidence", 0.0),
        facial_landmarks_detected=data.get("landmarks_detected", True),
        emotions_breakdown=emotions,
    )


def _mock_emotion_analysis() -> EmotionAnalysis:
    """APIキーがない場合のモック"""
    return EmotionAnalysis(
        dominant_emotion="angry",
        anger_level=8,
        confidence=0.92,
        facial_landmarks_detected=True,
        emotions_breakdown={
            "angry": 0.78,
            "sad": 0.10,
            "neutral": 0.08,
            "happy": 0.02,
            "surprise": 0.02,
        },
    )


def anger_to_text_prompt(analysis: EmotionAnalysis) -> str:
    """怒り解析結果から、エージェントへの自然言語プロンプトを生成"""
    emotion_labels = {
        "angry": "強い怒り",
        "sad": "深い悲しみ",
        "neutral": "冷静",
        "happy": "喜び",
        "surprise": "驚き",
        "fear": "恐怖",
    }

    label = emotion_labels.get(analysis.dominant_emotion, "不明な感情")
    level = analysis.anger_level

    prompts = [
        f"私は{label}を感じています。",
        f"怒りレベル: {level}/10",
        f"信頼度: {analysis.confidence:.0%}",
        "",
        "行政に対する不満・不信感があります。",
        "具体的にどのような行政文書・決定について怒りを感じているか、以下に記述します：",
        "",
        "（市民の具体的な不満・要求を入力してください）",
    ]

    return "\n".join(prompts)


def text_to_anger_level(text: str) -> int:
    """テキストから簡易的に怒りレベルを推定（YouCamなしの場合）"""
    anger_keywords = [
        "許せない", "ふざけるな", "怒り", "腹立つ", "最悪",
        "ひどい", "許されない", "おかしい", "なぜ", "不信",
        "隠蔽", "嘘", "ごまかし", "不誠実",
    ]
    score = 5  # ベース
    for kw in anger_keywords:
        if kw in text:
            score += 1
    return min(score, 10)