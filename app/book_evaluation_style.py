from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .position_importance_ranker import PositionImportanceRanking
from .position_interpretation import PositionInterpretationPackage


BOOK_EVALUATION_STYLE_VERSION = "1.2"
EvaluationAction = Literal[
    "verdict",
    "mechanism",
    "consequence",
    "comparison",
    "opponent_resource",
    "practical_plan",
]


class BookEvaluationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: EvaluationAction
    instruction: str = Field(min_length=1)
    required: bool


class BookEvaluationStyle(BaseModel):
    """Book-derived explanation order without source-position content."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.2"] = BOOK_EVALUATION_STYLE_VERSION
    method: Literal["judgment_reason_consequence_comparison"] = (
        "judgment_reason_consequence_comparison"
    )
    steps: list[BookEvaluationStep] = Field(default_factory=list, min_length=4, max_length=6)
    tone: Literal["decisive", "measured", "cautious"]
    maximum_main_points: int = Field(default=1, ge=1, le=3)
    narrative_path: list[str] = Field(default_factory=lambda: [
        "局面矛盾：先用一句话说明眼下最需要解决什么，只交代理解这个问题必需的局面条件。",
        "选择与代价：解释实战着的客观作用，再用已验证回应说明效果或代价；需要比较时始终围绕同一个问题。",
        "棋理启示：把已经证明的机制归纳成一个具体检查方法，不重复评价，不增加新计划。",
    ])
    prose_rules: list[str] = Field(default_factory=lambda: [
        "采用经典棋书共有的判断、因果和变化组织方法，不仿写某位作者的独特文风。",
        "正文是一段连贯的讲解，不显示思考步骤或固定小标题；句子之间要说明因果，不拼接标签。",
        "把着法写成解决问题的选择：说明改变了什么、为什么有用，以及给对手留下什么已验证机会。",
        "术语后立即结合具体棋子、格子或作用作通俗解释；比喻少而准确，解释后回到棋盘事实。",
        "先讲为什么值得看这条变化，再由路线区证明；多步后果必须保留中间条件，不能写成即时结果。",
        "只解释选择的客观吸引力，不推断棋手害怕、贪心、故意诱敌等心理，也不声称某错误在人群中更常见。",
        "语气服从证据，不为戏剧性添加致命、唯一、必然或陷阱；证据不足不能套用平稳或等待步结论。",
    ])
    reader_checks: list[str] = Field(
        default_factory=lambda: [
            "读者能用一句话复述这个局面的重点",
            "读者能说明该重点成立的具体原因",
            "读者知道相似局面中下一次应先检查什么",
        ],
        min_length=3,
        max_length=3,
    )
    forbidden_claims: list[str] = Field(default_factory=list, min_length=4)
    boundary: str = (
        "该框架只迁移经典棋书的评价顺序和语气控制；不迁移来源局面的棋子、格子、"
        "着法、评价、胜负、计划或主题判断。"
    )

    def prompt_payload(self) -> dict[str, object]:
        return self.model_dump(exclude={"version"})


def build_book_evaluation_style(
    interpretation: PositionInterpretationPackage,
    importance: PositionImportanceRanking,
) -> BookEvaluationStyle:
    """Choose a human evaluation method from current verified program scope."""
    objective = interpretation.objective.kind
    primary = importance.ranked_themes[0] if importance.ranked_themes else None
    immediate = bool(primary and primary.decision_priority == "now")
    decisive = immediate or objective == "forcing_tactics"
    cautious = importance.confidence in {"low", "unknown"}
    tone: Literal["decisive", "measured", "cautious"] = (
        "decisive" if decisive else "cautious" if cautious else "measured"
    )

    comparison_required = objective in {
        "dynamic_balance", "move_quality_explanation", "winning_conversion",
    }
    resource_required = objective in {
        "forcing_tactics", "attack_conversion", "winning_conversion",
    }
    plan_required = objective in {
        "strategic_improvement", "endgame_plan", "winning_conversion",
    }
    return BookEvaluationStyle(
        tone=tone,
        steps=[
            BookEvaluationStep(
                action="verdict",
                instruction=(
                    "第一句直接指出当前最重要的局面矛盾及其紧迫程度，让读者明白眼下争的是什么；"
                    "只讲一个核心判断，语气强度必须服从程序评价边界。"
                ),
                required=True,
            ),
            BookEvaluationStep(
                action="mechanism",
                instruction=(
                    "随后先说明该走法的客观作用及其值得考虑的原因，再解释它为何有效或为何没有奏效；"
                    "必须落到具体棋子、格子、线路、兵形或着法次序。"
                ),
                required=True,
            ),
            BookEvaluationStep(
                action="consequence",
                instruction=(
                    "说明程序证据支持的具体效果或代价；没有后果证据时只解释已确认作用，不补写惩罚；"
                    "变化只用于证明这段解释，不能代替解释。"
                ),
                required=True,
            ),
            BookEvaluationStep(
                action="comparison",
                instruction="按decisionContext的比较边界解释两种选择如何处理同一问题；相同首着不自我比较，小差距不强分高下，证据不足不倒推原因。",
                required=comparison_required,
            ),
            BookEvaluationStep(
                action="opponent_resource",
                instruction="指出对手最实际的防守或反制资源；没有程序证据时明确省略。",
                required=resource_required,
            ),
            BookEvaluationStep(
                action="practical_plan",
                instruction="有程序计划证据才说明实施条件和反击；结尾从本段已证明的机制提炼类似局面先检查什么，不另造计划。",
                required=plan_required,
            ),
        ],
        forbidden_claims=[
            "不得为了形成鲜明判断而夸大程序评价强度",
            "不得把候选路线内部事件写成当前已经存在的威胁",
            "不得补写程序没有验证的替代着法、计划或对手资源",
            "不得用棋书式权威语气掩盖证据不足",
            "不得把静态特征自动解释为当前评价的因果来源",
        ],
    )
