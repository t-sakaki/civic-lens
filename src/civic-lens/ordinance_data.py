"""Civic Lens — 条例データ

5自治体分の情報公開条例を構造化して保持。
実案件（安城市含む）の経験を反映した正確なデータ。

ハッカソン用にコンパクトに、本番では条例全文をCloud Storageに格納してRAG。
"""
from typing import Dict, List, Optional
from pydantic import BaseModel


class DisclosureGround(BaseModel):
    """不開示事由（条例第◯条第◯号）"""
    number: str  # "第2号"
    name: str    # "法人情報"
    description: str
    exception: str  # 公益性例外の条文


class OrdinanceInfo(BaseModel):
    """条例情報"""
    authority: str          # "安城市"
    authority_type: str     # "市長"/"市議会"/"県知事" 等
    ordinance_name: str     # "安城市情報公開条例"
    ordinance_id: str       # ファイル番号
    enacted: str            # 制定日
    request_form: str       # 請求書様式
    request_deadline_days: int  # 処分庁の開示決定期間（原則）
    extension_days: int     # 延長可能日数
    review_period_days: int # 審査請求期間（不開示決定後）
    non_disclosure_grounds: List[DisclosureGround]
    review_authority: str   # 審査会（諮問先）
    contact: str            # 窓口


# 5自治体分の条例データ
ORDINANCES: Dict[str, OrdinanceInfo] = {
    "anjo-city": OrdinanceInfo(
        authority="安城市",
        authority_type="市長",
        ordinance_name="安城市情報公開条例",
        ordinance_id="平成12年安城市条例第31号",
        enacted="平成12年12月25日",
        request_form="安城市情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第7条第1号",
                name="個人情報",
                description="個人に関する情報（事業を営む個人の当該事業に関する情報を除く。）で、特定の個人を識別することができるもの",
                exception="人の生命、健康、生活又は財産を保護するため、公にすることが必要であると認められる情報は開示"
            ),
            DisclosureGround(
                number="第7条第2号",
                name="法人情報",
                description="法人その他の団体（国、独立行政法人等、地方公共団体及び地方独立行政法人を除く。以下「法人等」という。）に関する情報又は事業を営む個人の当該事業に関する情報であって、公にすることにより、当該法人等又は当該個人の権利、競争上の地位その他正当な利益を害するおそれがあるもの",
                exception="人の生命、健康、生活又は財産を保護するため、公にすることが必要であると認められる情報は公開は"
            ),
            DisclosureGround(
                number="第7条第3号",
                name="事務執行影響",
                description="市の機関の内部又は相互間における審議、検討又は協議に関する情報であって、公にすることにより、率直な意見の交換若しくは意思決定の中立性が不当に損なわれるおそれ、不当に市民の間に混乱を生じさせるおそれ又は特定の者に不当に利益を与え若しくは不利益を及ぼすおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第7条第4号",
                name="事務事業情報",
                description="市の機関又は国等の機関が行う検査、監査、取締り、徴税、争訟、交渉、人事、試験、入札、許認可、経営方針、財産の運用、所得の徴収、契約の締結、用地の取得その他の事務又は事業に関する情報であって、公にすることにより、当該事務若しくは事業の性質上、当該事務又は事業の適正な遂行に支障を及ぼすおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="安城市情報公開審査审查会",
        contact="安城市役所 総務部 総務課 情報公開室"
    ),
    "nagoya-city": OrdinanceInfo(
        authority="名古屋市",
        authority_type="市長",
        ordinance_name="名古屋市情報公開条例",
        ordinance_id="平成12年名古屋市条例第12号",
        enacted="平成12年3月27日",
        request_form="名古屋市情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第7条第1号",
                name="個人情報",
                description="個人に関する情報（事業を営む個人の当該事業に関する情報を除く）で、特定の個人を識別できるもの",
                exception=""
            ),
            DisclosureGround(
                number="第7条第2号",
                name="法人情報",
                description="法人その他の団体に関する情報で、公にすることにより当該法人等の正当な利益を害するおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第7条第3号",
                name="審議検討情報",
                description="審議・検討・協議に関する情報で、公にすることで率直な意見交換等が不当に損なわれるおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="名古屋市情報公開審査会",
        contact="名古屋市役所 総務局 市政情報室"
    ),
    "okazaki-city": OrdinanceInfo(
        authority="岡崎市",
        authority_type="市長",
        ordinance_name="岡崎市情報公開条例",
        ordinance_id="平成12年岡崎市条例第33号",
        enacted="平成12年12月26日",
        request_form="岡崎市情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第6条第1号",
                name="個人情報",
                description="個人に関する情報で特定の個人を識別できるもの",
                exception=""
            ),
            DisclosureGround(
                number="第6条第2号",
                name="法人情報",
                description="法人等に関する情報で、公にすることにより当該法人等の正当な利益を害するおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="岡崎市情報公開審査会",
        contact="岡崎市役所 企画財政部 行政経営課"
    ),
    "aichi-pref": OrdinanceInfo(
        authority="愛知県",
        authority_type="知事",
        ordinance_name="愛知県情報公開条例",
        ordinance_id="平成12年愛知県条例第37号",
        enacted="平成12年10月13日",
        request_form="愛知県情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第7条第1号",
                name="個人情報",
                description="個人に関する情報",
                exception=""
            ),
            DisclosureGround(
                number="第7条第2号",
                name="法人情報",
                description="法人等に関する情報で、公にすることにより当該法人等の正当な利益を害するおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第7条第3号",
                name="事務執行影響",
                description="県が行う事務又は事業に関する情報のうち、その性質上、公にすることにより当該事務の適正な執行に支障を及ぼすおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="愛知県情報公開審査会",
        contact="愛知県庁 県民文化部 県民総務課 広報広聴室"
    ),
    "aichi-assembly": OrdinanceInfo(
        authority="愛知県議会",
        authority_type="議会",
        ordinance_name="愛知県議会情報公開条例",
        ordinance_id="平成12年愛知県条例第38号",
        enacted="平成12年10月13日",
        request_form="愛知県議会情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第7条第1号",
                name="個人情報",
                description="個人に関する情報",
                exception=""
            ),
            DisclosureGround(
                number="第7条第2号",
                name="議会活動情報",
                description="議員の政治活動に関する情報",
                exception=""
            ),
            DisclosureGround(
                number="第7条第3号",
                name="議会運営情報",
                description="議会の運営に関する情報で、公にすることにより議会運営に支障を及ぼすおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="愛知県議会情報公開審査会",
        contact="愛知県議会 事務局 議事課"
    ),
}


# ======================================================================
# 警察本部・警視庁（公安委員会規則による情報公開）
# ======================================================================
POLICE_AUTHORITIES: Dict[str, OrdinanceInfo] = {
    "metropolitan-police": OrdinanceInfo(
        authority="警視庁",
        authority_type="警察本部",
        ordinance_name="警視庁情報公開規程",
        ordinance_id="平成13年警察庁訓令第9号（警視庁）",
        enacted="平成13年4月1日",
        request_form="警視庁情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第5条第1号",
                name="個人情報",
                description="個人に関する情報で、特定の個人を識別できるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第2号",
                name="法人情報",
                description="法人等の正当な利益を害するおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第3号",
                name="捜査情報",
                description="捜査の着手・手法・関係者の特定・犯人識別情報等で、開示により捜査・公判・法執行に支障を及ぼすおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第4号",
                name="公共安全情報",
                description="テロ対策・警備情報・要人警護情報等で、開示により公共の安全と秩序の維持に支障を及ぼすおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第5号",
                name="事務執行影響",
                description="警察の事務事業の性質上、公にすることにより当該事務の適正な遂行に支障を及ぼすおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="警視庁情報公開審査会",
        contact="警視庁総務部 情報公開センター"
    ),
    "aichi-police": OrdinanceInfo(
        authority="愛知県警察本部",
        authority_type="警察本部",
        ordinance_name="愛知県警察本部情報公開規程",
        ordinance_id="愛知県公安委員会規則第10号",
        enacted="平成13年4月1日",
        request_form="愛知県警察本部情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第5条第1号",
                name="個人情報",
                description="個人に関する情報で特定の個人を識別できるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第2号",
                name="法人情報",
                description="法人等の正当な利益を害するおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第3号",
                name="捜査情報",
                description="捜査関係事項で、開示により捜査・公判・法執行に支障を及ぼすおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第4号",
                name="公共安全情報",
                description="警備・要人警護・テロ対策情報で、公共の安全と秩序の維持に支障を及ぼすおそれがあるもの",
                exception=""
            ),
            DisclosureGround(
                number="第5条第5号",
                name="事務執行影響",
                description="警察の事務事業の性質上、公にすることにより当該事務の適正な遂行に支障を及ぼすおそれがあるもの",
                exception=""
            ),
        ],
        review_authority="愛知県警察本部情報公開審査会",
        contact="愛知県警察本部 総務課 情報公開室"
    ),
    "kanagawa-police": OrdinanceInfo(
        authority="神奈川県警察本部",
        authority_type="警察本部",
        ordinance_name="神奈川県警察本部情報公開規程",
        ordinance_id="神奈川県公安委員会規則",
        enacted="平成13年4月1日",
        request_form="神奈川県警察本部情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第5条第1号",
                name="個人情報",
                description="個人に関する情報",
                exception=""
            ),
            DisclosureGround(
                number="第5条第2号",
                name="法人情報",
                description="法人等の正当利益を害するおそれ",
                exception=""
            ),
            DisclosureGround(
                number="第5条第3号",
                name="捜査情報",
                description="捜査・公判に支障を及ぼすおそれ",
                exception=""
            ),
            DisclosureGround(
                number="第5条第4号",
                name="公共安全情報",
                description="公共の安全と秩序維持に支障",
                exception=""
            ),
        ],
        review_authority="神奈川県警察本部情報公開審査会",
        contact="神奈川県警察本部 総務課"
    ),
    "osaka-police": OrdinanceInfo(
        authority="大阪府警察本部",
        authority_type="警察本部",
        ordinance_name="大阪府警察本部情報公開規程",
        ordinance_id="大阪府公安委員会規則",
        enacted="平成13年4月1日",
        request_form="大阪府警察本部情報公開請求書",
        request_deadline_days=30,
        extension_days=30,
        review_period_days=90,
        non_disclosure_grounds=[
            DisclosureGround(
                number="第5条第1号",
                name="個人情報",
                description="個人に関する情報",
                exception=""
            ),
            DisclosureGround(
                number="第5条第2号",
                name="法人情報",
                description="法人等の正当利益を害するおそれ",
                exception=""
            ),
            DisclosureGround(
                number="第5条第3号",
                name="捜査情報",
                description="捜査・公判に支障を及ぼすおそれ",
                exception=""
            ),
            DisclosureGround(
                number="第5条第4号",
                name="公共安全情報",
                description="公共の安全と秩序維持に支障",
                exception=""
            ),
        ],
        review_authority="大阪府警察本部情報公開審査会",
        contact="大阪府警察本部 総務課"
    ),
}


# 警察特有・反論ロジック追加
POLICE_COUNTER_ARGUMENTS = {
    "第5条第3号": [
        "「捜査に支障」は抽象的では足りず、具体的・個別的な支障が必要。事件",
        "既に終結した事件の情報は開示すべき時期にきている。",
        "統計情報・統計値は捜査の支障とはならない。",
        "犯人識別情報等は黒塗り（マスキング）で部分開示が相当。",
        "既に公判で公開された情報は捜査情報の対象外。",
    ],
    "第5条第4号": [
        "警備情報は時限性があり、一定期間経過後は開示可能。",
        "統計的な事案数・検挙数等は公共の安全に支障しない。",
        "公共の安全と秩序の維持は、抽象的不安では足りない。",
        "テロ対策手法自体ではなく、その運用実績等は開示可能。",
    ],
    "第5条第5号": [
        "警察の事務であっても意思決定後の情報は開示すべき。",
        "人事・予算・契約情報等は通常の行政情報と同じ取扱い。",
        "内部通達・運用要領等は政策判断の基礎情報として公益性が高い。",
    ],
}


def get_ordinance(authority_key: str) -> Optional[OrdinanceInfo]:
    """条例を取得（自治体・警察両方）"""
    if authority_key in ORDINANCES:
        return ORDINANCES[authority_key]
    if authority_key in POLICE_AUTHORITIES:
        return POLICE_AUTHORITIES[authority_key]
    return None


def list_authorities() -> List[str]:
    """対応自治体一覧（自治体・警察両方）"""
    return list(ORDINANCES.keys()) + list(POLICE_AUTHORITIES.keys())


def is_police_authority(authority_key: str) -> bool:
    """警察機関かどうか"""
    return authority_key in POLICE_AUTHORITIES


# 不開示事由の典型的反論ロジック（自治体用）
COMMON_COUNTER_ARGUMENTS = {
    "第7条第2号": [
        "「権利、競争上の地位その他正当な利益を害するおそれ」は、抽象的・主観的な理由ではなく、具体的・実質的な害益のおそれでなければならない（最判平14.2.8）。",
        "当該情報が公になっても、法人等の正当な利益が実際に害される具体的な根拠を行政は示す責任がある。",
        "「競争上の地位」は事業者間の競争関係を念頭に置くものであり、本件のような内部資料には該当しない。",
        "既に公になっている情報と同内容の情報は、法人情報に該当しない。",
        "法人等の代表者個人の意見・判断に関する情報で、開示しても法人の正当な利益を害しないものは除く。",
    ],
    "第7条第3号": [
        "審議・検討中の情報は、意思決定後の情報については原則開示すべき（最判平11.12.16）。",
        "「率直な意見の交換が不当に損なわれるおそれ」は、単なる可能性では足りず、具体的・実質的なおそれが必要。",
        "本件は既に決定された事項であり、審議中の情報ではない。",
        "本件情報は事実経過の記録であり、意見・判断そのものではない。",
    ],
    "第7条第4号": [
        "「事務の適正な遂行に支障を及ぼすおそれ」は、抽象的・一般的不安では足りず、具体的・個別的な支障が必要。",
        "同種の情報は他の自治体で開示されている実例があり、支障は認められない。",
        "事務の性質は時系列的に変化し、意思決定後の情報は開示すべき時期に来ている。",
        "本件情報は統計的・客観的数値であり、事務執行に影響する性質のものではない。",
    ],
}