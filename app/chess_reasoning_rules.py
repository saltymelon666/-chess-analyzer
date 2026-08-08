from __future__ import annotations

from collections import Counter
import hashlib
from typing import TYPE_CHECKING, Iterable, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .chess_facts import ChessFactPackage
    from .strategic_plans import StrategicPlanPackage
    from .threat_analysis import ThreatPackage


RULE_PACKAGE_VERSION = "1.0"
RuleCategory = Literal[
    "tactics",
    "king_safety",
    "pawn_structure",
    "piece_coordination",
    "space_and_lines",
    "plans",
    "evaluation",
]
AutomationMode = Literal[
    "board_exact",
    "board_heuristic",
    "stockfish_route",
    "stockfish_multipv",
]
SignalScope = Literal["current_position", "candidate_route", "interpretation"]
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}
PIECE_NAMES = {
    chess.PAWN: "兵",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}


class ChessReasoningRuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    name_zh: str
    category: RuleCategory
    automation: AutomationMode
    evidence_requirement: str
    allowed_claim: str
    forbidden_claim: str
    confidence_ceiling: Literal["medium", "high"]


class ChessReasoningSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    rule_id: str
    category: RuleCategory
    side: Literal["white", "black", "both"]
    scope: SignalScope = "current_position"
    summary: str
    evidence: list[str] = Field(min_length=1)
    squares: list[str] = Field(default_factory=list)
    moves: list[str] = Field(default_factory=list)
    confidence: Literal["medium", "high"]


class ChessReasoningRulePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = RULE_PACKAGE_VERSION
    position_id: str
    catalog_size: int
    signals: list[ChessReasoningSignal] = Field(default_factory=list)
    retrieval_themes: list[str] = Field(default_factory=list)
    exact_signal_count: int = 0
    heuristic_signal_count: int = 0
    engine_signal_count: int = 0


def _rule(
    rule_id: str,
    name: str,
    category: RuleCategory,
    automation: AutomationMode,
    allowed: str,
    *,
    forbidden: str = "不得仅凭该信号声称局面胜负已定",
) -> ChessReasoningRuleDefinition:
    requirement = {
        "board_exact": "python-chess当前棋盘或当前合法走法可直接验证",
        "board_heuristic": "python-chess事实满足保守阈值；只能使用倾向性措辞",
        "stockfish_route": "至少一条经验证Stockfish候选路线明确展示该事件",
        "stockfish_multipv": "至少两条评价稳定的经验证Stockfish路线支持同一计划或来源",
    }[automation]
    return ChessReasoningRuleDefinition(
        rule_id=rule_id,
        name_zh=name,
        category=category,
        automation=automation,
        evidence_requirement=requirement,
        allowed_claim=allowed,
        forbidden_claim=forbidden,
        confidence_ceiling="high" if automation == "board_exact" else "medium",
    )


def _catalog_group(
    category: RuleCategory,
    rows: Iterable[tuple[str, str, AutomationMode, str]],
) -> list[ChessReasoningRuleDefinition]:
    return [_rule(rule_id, name, category, mode, allowed) for rule_id, name, mode, allowed in rows]


RULE_CATALOG: tuple[ChessReasoningRuleDefinition, ...] = tuple([
    *_catalog_group("tactics", [
        ("tactic.immediate_capture", "当前合法吃子", "board_exact", "当前存在具体合法吃子着"),
        ("tactic.favorable_capture", "有利交换吃子", "board_heuristic", "按子力价值看该吃子值得优先计算"),
        ("tactic.hanging_piece_capture", "吃无保护棋子", "board_exact", "可直接吃掉当前没有保护的棋子"),
        ("tactic.immediate_check", "当前合法将军", "board_exact", "当前存在具体合法将军着"),
        ("tactic.immediate_checkmate", "当前一步将杀", "board_exact", "当前存在python-chess验证的一步将杀"),
        ("tactic.fork", "捉双", "board_exact", "某个当前合法走法同时攻击至少两个重要目标"),
        ("tactic.double_attack", "双重攻击", "board_exact", "某个当前合法走法同时制造两个具体攻击点"),
        ("tactic.discovered_check", "闪将", "board_exact", "移开棋子后由另一枚棋子形成将军"),
        ("tactic.absolute_pin", "绝对牵制", "board_exact", "棋子因身后是王而不能合法移动"),
        ("tactic.relative_pin", "相对牵制", "stockfish_route", "路线显示移动被牵制棋子会丢失更高价值目标"),
        ("tactic.skewer", "串击", "stockfish_route", "路线显示高价值棋子退开后身后目标被吃"),
        ("tactic.xray", "X光攻击", "board_heuristic", "同线棋子隔着一个阻挡子相互作用"),
        ("tactic.trapped_piece", "困住棋子", "board_heuristic", "受攻棋子合法活动格极少"),
        ("tactic.remove_defender", "消除防守者", "stockfish_route", "路线先交换或吃掉关键防守子再取得目标"),
        ("tactic.overloaded_defender", "过载防守", "board_heuristic", "同一防守子同时承担两个重要防守任务"),
        ("tactic.deflection", "引离", "stockfish_route", "路线迫使防守子离开关键防守点"),
        ("tactic.decoy", "诱引", "stockfish_route", "路线迫使目标棋子进入具体战术格"),
        ("tactic.interference", "干扰", "stockfish_route", "路线在攻击者和防守者之间插入棋子"),
        ("tactic.clearance", "腾挪清线", "stockfish_route", "路线先腾空格子或线路再使用该线路"),
        ("tactic.zwischenzug", "中间着", "stockfish_route", "路线在预期回吃前插入更强制的着法"),
        ("tactic.sacrifice", "战术性牺牲", "stockfish_route", "引擎路线支持暂时投入物质以换取可验证收益"),
        ("tactic.promotion", "升变威胁", "board_exact", "当前存在合法升变着"),
        ("tactic.perpetual_check", "长将候选", "stockfish_multipv", "多条稳定路线重复出现连续将军"),
        ("tactic.back_rank_mate", "底线将杀", "board_exact", "车或后在底线完成当前合法一步将杀"),
    ]),
    *_catalog_group("king_safety", [
        ("king.in_check", "王当前被将军", "board_exact", "当前行棋方的王正在被将军"),
        ("king.mate_threat", "杀王威胁", "stockfish_route", "经验证路线给出直接或准备型将杀威胁"),
        ("king.ring_pressure", "王圈受压", "board_heuristic", "王周围多个格子正受敌方控制"),
        ("king.multiple_attackers", "多子攻王", "board_heuristic", "至少两枚敌子直接作用于王圈"),
        ("king.missing_pawn_shield", "王前兵盾缺失", "board_heuristic", "王前方相邻兵盾不完整"),
        ("king.open_file", "通向王的开放线", "board_exact", "王所在或相邻竖线没有兵阻挡"),
        ("king.semi_open_file", "通向王的半开放线", "board_exact", "敌方在王附近拥有可用半开放线"),
        ("king.weak_light_squares", "王翼白格薄弱", "board_heuristic", "王圈多个白格受攻且缺少本方兵控制"),
        ("king.weak_dark_squares", "王翼黑格薄弱", "board_heuristic", "王圈多个黑格受攻且缺少本方兵控制"),
        ("king.enemy_queen_near", "敌后接近王", "board_exact", "敌后位于王两格范围内"),
        ("king.battery", "重子或象后电池攻王", "board_heuristic", "至少两枚敌方远程子在同线指向王区"),
        ("king.low_flight_squares", "王的逃跑格不足", "board_exact", "王当前合法安全邻格不超过一个"),
        ("king.central_king", "王仍在中心", "board_exact", "王当前位于c至f线的中央区域"),
        ("king.opposite_wings", "异侧王位", "board_exact", "双方王分别位于棋盘两翼"),
    ]),
    *_catalog_group("pawn_structure", [
        ("pawn.isolated", "孤兵", "board_exact", "该兵相邻兵线没有本方兵"),
        ("pawn.doubled", "叠兵", "board_exact", "同一兵线上有两枚本方兵"),
        ("pawn.tripled", "三叠兵", "board_exact", "同一兵线上有至少三枚本方兵"),
        ("pawn.backward", "落后兵", "board_heuristic", "该兵缺少相邻兵支援且推进格受敌兵控制"),
        ("pawn.passed", "通路兵", "board_exact", "前方同线及相邻线没有敌兵"),
        ("pawn.connected_passed", "相连通路兵", "board_exact", "相邻兵线存在本方通路兵"),
        ("pawn.protected_passed", "受保护通路兵", "board_exact", "通路兵由本方兵直接保护"),
        ("pawn.candidate_passed", "候选通路兵", "board_heuristic", "本方局部兵数足以通过交换形成通路兵"),
        ("pawn.chain", "兵链", "board_exact", "至少三枚兵通过兵保护连接"),
        ("pawn.islands", "兵岛", "board_exact", "本方兵分布形成多个互不相邻兵群"),
        ("pawn.hanging", "悬兵", "board_heuristic", "相邻两兵缺少其他兵保护且可能成为目标"),
        ("pawn.iqp", "孤立后兵", "board_exact", "d线兵为孤兵"),
        ("pawn.queenside_majority", "后翼兵多数", "board_exact", "a至d线本方兵数量更多"),
        ("pawn.kingside_majority", "王翼兵多数", "board_exact", "e至h线本方兵数量更多"),
        ("pawn.lever", "兵接触与突破点", "board_exact", "本方兵可以合法推进并直接挑战敌兵"),
        ("pawn.space_chain", "前伸兵链", "board_heuristic", "多枚本方兵越过中线并相互支援"),
    ]),
    *_catalog_group("piece_coordination", [
        ("piece.undefended", "无保护棋子", "board_exact", "该非王棋子当前没有本方保护者"),
        ("piece.underprotected", "受攻且保护不足", "board_exact", "攻击者数量多于保护者数量"),
        ("piece.active", "活跃棋子", "board_heuristic", "该棋子当前作用格较多"),
        ("piece.constrained", "受限棋子", "board_heuristic", "该棋子当前作用格很少"),
        ("piece.outpost", "前哨", "board_exact", "马或象位于本方兵保护且不受敌兵攻击的前进格"),
        ("piece.bishop_pair", "双象", "board_exact", "一方同时保有两只象"),
        ("piece.bad_bishop", "坏象", "board_heuristic", "象与多数本方中心兵位于同色且活动受限"),
        ("piece.knight_rim", "边马", "board_exact", "马位于a线或h线"),
        ("piece.rook_open_file", "车占开放线", "board_exact", "车位于双方都没有兵的竖线"),
        ("piece.rook_semi_open", "车占半开放线", "board_exact", "车所在竖线没有本方兵但仍有敌兵"),
        ("piece.rook_seventh", "车侵入第七横线", "board_exact", "车位于对方第二横线"),
        ("piece.connected_rooks", "双车联通", "board_exact", "两车位于同线且中间没有棋子"),
        ("piece.battery", "远程子电池", "board_heuristic", "两枚本方远程子在同线协同"),
        ("piece.development_lag", "出子落后", "board_exact", "开中局仍有多枚轻子停在初始格"),
        ("piece.worst_piece", "最差棋子候选", "board_heuristic", "本方非兵棋子中该子活动格最少"),
    ]),
    *_catalog_group("space_and_lines", [
        ("space.advantage", "空间优势候选", "board_heuristic", "一方在敌方半场控制更多安全格"),
        ("space.center_control", "中心控制", "board_exact", "一方控制更多d4、e4、d5、e5中心格"),
        ("line.open_file_control", "开放线控制", "board_exact", "车或后已经占据开放线"),
        ("line.semi_open_pressure", "半开放线压力", "board_exact", "车或后在半开放线上面对敌兵"),
        ("line.open_diagonal", "开放斜线", "board_exact", "象或后拥有较长且无己子阻挡的斜线"),
        ("space.weak_square", "弱格候选", "board_heuristic", "前进格不受敌兵控制且可被本方棋子利用"),
        ("space.restriction", "限制对方棋子", "board_heuristic", "对方多枚非兵棋子活动格很少"),
    ]),
    *_catalog_group("plans", [
        ("plan.improve_worst_piece", "改善最差棋子", "stockfish_multipv", "多条路线用不同着法改善同一受限棋子"),
        ("plan.center_break", "准备中心突破", "stockfish_multipv", "多条路线支持同一中心兵突破"),
        ("plan.occupy_open_file", "占据开放线", "stockfish_multipv", "多条路线把重子调到同一开放线"),
        ("plan.activate_rook", "激活车", "stockfish_multipv", "多条路线提升同一辆车的作用"),
        ("plan.king_safety", "改善王安全", "stockfish_multipv", "多条路线采用明确的王安全措施"),
        ("plan.attack_weak_pawn", "攻击弱兵", "stockfish_multipv", "多条路线集中攻击同一结构弱兵"),
        ("plan.create_passed_pawn", "制造通路兵", "stockfish_multipv", "多条路线通过兵突破形成通路兵"),
        ("plan.simplify", "简化进入残局", "stockfish_multipv", "优势方多条路线主动交换并保持评价"),
        ("plan.activate_king", "残局激活王", "stockfish_multipv", "残局多条路线让王接近中心或关键兵"),
        ("plan.prophylaxis", "预防对手计划", "stockfish_route", "路线先限制对手明确资源，且忽略测试显示有代价"),
    ]),
    *_catalog_group("evaluation", [
        ("evaluation.material_source", "物质是评价来源", "board_exact", "物质差可作为当前评价来源之一"),
        ("evaluation.tactical_source", "战术是评价来源", "stockfish_route", "强制路线说明评价主要来自战术"),
        ("evaluation.king_safety_source", "王安全是评价来源", "stockfish_multipv", "多条路线持续利用同一王安全问题"),
        ("evaluation.structure_source", "兵结构是评价来源", "stockfish_multipv", "多条路线围绕同一兵形弱点展开"),
        ("evaluation.activity_source", "子力活动是评价来源", "stockfish_multipv", "多条路线稳定改善活动或限制对方"),
        ("evaluation.initiative_gate", "主动权证据门禁", "stockfish_multipv", "只有动态威胁和多条被迫应对路线同时成立才确认主动权"),
    ]),
])
RULES_BY_ID = {rule.rule_id: rule for rule in RULE_CATALOG}
if len(RULES_BY_ID) != len(RULE_CATALOG):
    raise RuntimeError("duplicate chess reasoning rule id")


class ChessReasoningRuleEngine:
    """Emit conservative rule signals; catalog entries never imply an automatic hit."""

    def evaluate(
        self,
        fen: str,
        *,
        fact_package: "ChessFactPackage | None" = None,
        threat_package: "ThreatPackage | None" = None,
        plan_package: "StrategicPlanPackage | None" = None,
    ) -> ChessReasoningRulePackage:
        board = chess.Board(fen)
        signals: list[ChessReasoningSignal] = []
        self._tactics(board, signals)
        self._king_safety(board, signals)
        self._pawn_structure(board, signals)
        self._piece_coordination(board, signals)
        self._space_and_lines(board, signals)
        self._engine_packages(fact_package, threat_package, plan_package, signals)
        signals = self._deduplicate(signals)
        for index, signal in enumerate(signals, start=1):
            signal.signal_id = f"rule_signal_{index}"
        themes = sorted({_retrieval_theme(signal.category, signal.rule_id) for signal in signals})
        modes = Counter(RULES_BY_ID[signal.rule_id].automation for signal in signals)
        return ChessReasoningRulePackage(
            position_id=f"position:{hashlib.sha256(board.fen().encode()).hexdigest()[:16]}",
            catalog_size=len(RULE_CATALOG),
            signals=signals,
            retrieval_themes=themes,
            exact_signal_count=modes["board_exact"],
            heuristic_signal_count=modes["board_heuristic"],
            engine_signal_count=modes["stockfish_route"] + modes["stockfish_multipv"],
        )

    def _add(
        self,
        signals: list[ChessReasoningSignal],
        rule_id: str,
        side: str,
        summary: str,
        evidence: list[str],
        *,
        squares: Iterable[str] = (),
        moves: Iterable[str] = (),
        scope: SignalScope = "current_position",
        confidence: Literal["medium", "high"] | None = None,
    ) -> None:
        rule = RULES_BY_ID[rule_id]
        signals.append(ChessReasoningSignal(
            signal_id="pending",
            rule_id=rule_id,
            category=rule.category,
            side=side,
            scope=scope,
            summary=summary,
            evidence=evidence,
            squares=sorted(set(squares)),
            moves=sorted(set(moves)),
            confidence=confidence or rule.confidence_ceiling,
        ))

    def _tactics(self, board: chess.Board, signals: list[ChessReasoningSignal]) -> None:
        mover = _side(board.turn)
        for move in list(board.legal_moves):
            san = board.san(move)
            if board.is_capture(move):
                captured_square = _captured_square(board, move)
                captured = board.piece_at(captured_square)
                attacker = board.piece_at(move.from_square)
                if captured is not None and attacker is not None:
                    target = chess.square_name(captured_square)
                    self._add(signals, "tactic.immediate_capture", mover, f"当前可走{san}吃掉{target}{PIECE_NAMES[captured.piece_type]}",
                              [f"python-chess合法吃子: {move.uci()} / {san}"], squares=[target], moves=[move.uci()])
                    if not board.attackers(captured.color, captured_square):
                        self._add(signals, "tactic.hanging_piece_capture", mover, f"{san}可吃掉当前无保护的{target}{PIECE_NAMES[captured.piece_type]}",
                                  [f"{target}本方保护者数量为0", f"合法着{move.uci()}"], squares=[target], moves=[move.uci()])
                    if PIECE_VALUES[captured.piece_type] > PIECE_VALUES[attacker.piece_type]:
                        self._add(signals, "tactic.favorable_capture", mover, f"{san}以较低价值棋子吃较高价值棋子，值得优先计算",
                                  [f"攻击子价值{PIECE_VALUES[attacker.piece_type]}", f"目标价值{PIECE_VALUES[captured.piece_type]}"],
                                  squares=[chess.square_name(move.from_square), target], moves=[move.uci()])
            if board.gives_check(move):
                self._add(signals, "tactic.immediate_check", mover, f"当前有合法将军着{san}",
                          [f"python-chess验证将军: {move.uci()} / {san}"], moves=[move.uci()])
                after = board.copy(stack=False)
                after.push(move)
                if after.is_checkmate():
                    self._add(signals, "tactic.immediate_checkmate", mover, f"{san}是当前一步将杀",
                              [f"python-chess验证走后checkmate=true: {move.uci()}"], moves=[move.uci()])
                    piece = board.piece_at(move.from_square)
                    if piece and piece.piece_type in {chess.ROOK, chess.QUEEN} and chess.square_rank(move.to_square) in {0, 7}:
                        self._add(signals, "tactic.back_rank_mate", mover, f"{san}构成底线一步将杀",
                                  ["将杀棋子为车或后且落在底线"], moves=[move.uci()])
                moved_piece = after.piece_at(move.to_square)
                checkers = after.checkers()
                if moved_piece and move.to_square not in checkers and checkers:
                    self._add(signals, "tactic.discovered_check", mover, f"{san}移开线路后形成闪将",
                              [f"走后将军者位于{','.join(chess.square_name(sq) for sq in checkers)}，不是落子格"], moves=[move.uci()])
            if move.promotion:
                self._add(signals, "tactic.promotion", mover, f"当前有合法升变着{san}",
                          [f"python-chess验证promotion={chess.piece_name(move.promotion)}"], moves=[move.uci()])
            targets = _important_targets_after_move(board, move)
            if len(targets) >= 2:
                piece = board.piece_at(move.from_square)
                rule_id = "tactic.fork" if piece and piece.piece_type in {chess.KNIGHT, chess.PAWN} else "tactic.double_attack"
                self._add(signals, rule_id, mover, f"{san}同时攻击{targets[0][0]}和{targets[1][0]}",
                          [f"走后新攻击重要目标: {', '.join(square for square, _ in targets)}"],
                          squares=[square for square, _ in targets], moves=[move.uci()])
        for color in chess.COLORS:
            side = _side(color)
            for square, piece in board.piece_map().items():
                if piece.color != color or piece.piece_type == chess.KING:
                    continue
                if board.is_pinned(color, square):
                    self._add(signals, "tactic.absolute_pin", side, f"{chess.square_name(square)}{PIECE_NAMES[piece.piece_type]}被绝对牵制",
                              [f"python-chess is_pinned({side}, {chess.square_name(square)})=true"], squares=[chess.square_name(square)])
                mobility = _legal_piece_mobility(board, square)
                if mobility <= 1 and board.attackers(not color, square):
                    self._add(signals, "tactic.trapped_piece", side, f"受攻的{chess.square_name(square)}{PIECE_NAMES[piece.piece_type]}只有{mobility}个合法活动格",
                              [f"合法活动格={mobility}", f"敌方攻击者={len(board.attackers(not color, square))}"], squares=[chess.square_name(square)])

    def _king_safety(self, board: chess.Board, signals: list[ChessReasoningSignal]) -> None:
        if board.is_check():
            king = board.king(board.turn)
            self._add(signals, "king.in_check", _side(board.turn), "当前行棋方的王正在被将军",
                      ["python-chess board.is_check()=true"], squares=[chess.square_name(king)] if king is not None else [])
        zones = {}
        for color in chess.COLORS:
            side = _side(color)
            king = board.king(color)
            if king is None:
                continue
            name = chess.square_name(king)
            zone = {king, *board.attacks(king)}
            zones[color] = _wing(king)
            attacked = [sq for sq in zone if board.is_attacked_by(not color, sq)]
            attackers = {attacker for sq in zone for attacker in board.attackers(not color, sq)}
            safe_flights = [sq for sq in board.attacks(king) if board.piece_at(sq) is None and not board.is_attacked_by(not color, sq)]
            if len(attacked) >= 3:
                self._add(signals, "king.ring_pressure", side, f"{name}王圈有{len(attacked)}个格子受敌方控制",
                          [f"受控王圈格: {', '.join(chess.square_name(sq) for sq in attacked)}"], squares=[chess.square_name(sq) for sq in attacked])
            if len(attackers) >= 2:
                self._add(signals, "king.multiple_attackers", side, f"至少{len(attackers)}枚敌子作用于{name}王圈",
                          [f"攻击子格: {', '.join(chess.square_name(sq) for sq in sorted(attackers))}"], squares=[name, *(chess.square_name(sq) for sq in attackers)])
            if len(safe_flights) <= 1:
                self._add(signals, "king.low_flight_squares", side, f"{name}王当前安全空邻格只有{len(safe_flights)}个",
                          [f"安全空邻格: {', '.join(chess.square_name(sq) for sq in safe_flights) or '无'}"], squares=[name])
            if chess.square_file(king) in {2, 3, 4, 5}:
                self._add(signals, "king.central_king", side, f"{name}王当前仍位于中央区域",
                          [f"王位于{name}"], squares=[name])
            shield = _pawn_shield_squares(king, color)
            present = [sq for sq in shield if board.piece_at(sq) == chess.Piece(chess.PAWN, color)]
            if shield and len(present) <= 1:
                self._add(signals, "king.missing_pawn_shield", side, f"{name}王前兵盾仅保留{len(present)}/{len(shield)}枚兵",
                          [f"兵盾格: {', '.join(chess.square_name(sq) for sq in shield)}"], squares=[name, *(chess.square_name(sq) for sq in shield)])
            enemy_queens = [sq for sq in board.pieces(chess.QUEEN, not color) if chess.square_distance(sq, king) <= 2]
            if enemy_queens:
                self._add(signals, "king.enemy_queen_near", side, f"敌后已接近{name}王",
                          [f"敌后格: {', '.join(chess.square_name(sq) for sq in enemy_queens)}"], squares=[name, *(chess.square_name(sq) for sq in enemy_queens)])
            file_name = chess.FILE_NAMES[chess.square_file(king)]
            white_pawns = board.pieces(chess.PAWN, chess.WHITE) & chess.BB_FILES[chess.square_file(king)]
            black_pawns = board.pieces(chess.PAWN, chess.BLACK) & chess.BB_FILES[chess.square_file(king)]
            if not white_pawns and not black_pawns:
                self._add(signals, "king.open_file", side, f"{name}王位于没有兵的{file_name}开放线",
                          [f"{file_name}线双方兵数均为0"], squares=[name])
            elif not board.pieces(chess.PAWN, not color) & chess.BB_FILES[chess.square_file(king)]:
                self._add(signals, "king.semi_open_file", side, f"敌方可利用{name}附近的{file_name}半开放线",
                          [f"{file_name}线敌方无兵"], squares=[name])
            for light, rule_id in ((True, "king.weak_light_squares"), (False, "king.weak_dark_squares")):
                weak = [sq for sq in zone if bool(chess.BB_LIGHT_SQUARES & chess.BB_SQUARES[sq]) == light
                        and board.is_attacked_by(not color, sq) and not _attacked_by_pawn(board, color, sq)]
                if len(weak) >= 2:
                    self._add(signals, rule_id, side, f"{name}王圈有{len(weak)}个同色格受攻且缺少兵控制",
                              [f"弱格: {', '.join(chess.square_name(sq) for sq in weak)}"], squares=[chess.square_name(sq) for sq in weak])
        if zones.get(chess.WHITE) in {"kingside", "queenside"} and zones.get(chess.BLACK) in {"kingside", "queenside"} and zones[chess.WHITE] != zones[chess.BLACK]:
            self._add(signals, "king.opposite_wings", "both", "双方王分别位于棋盘两翼",
                      [f"白王在{zones[chess.WHITE]}，黑王在{zones[chess.BLACK]}"])

    def _pawn_structure(self, board: chess.Board, signals: list[ChessReasoningSignal]) -> None:
        for color in chess.COLORS:
            side = _side(color)
            pawns = sorted(board.pieces(chess.PAWN, color))
            file_counts = Counter(chess.square_file(sq) for sq in pawns)
            islands = _pawn_islands(file_counts)
            if islands >= 2:
                self._add(signals, "pawn.islands", side, f"本方兵形成{islands}个兵岛",
                          [f"有兵的竖线: {', '.join(chess.FILE_NAMES[file] for file in sorted(file_counts))}"])
            for file_index, count in file_counts.items():
                if count >= 2:
                    self._add(signals, "pawn.doubled", side, f"{chess.FILE_NAMES[file_index]}线有{count}枚叠兵",
                              [f"同线兵数={count}"], squares=[chess.square_name(sq) for sq in pawns if chess.square_file(sq) == file_index])
                if count >= 3:
                    self._add(signals, "pawn.tripled", side, f"{chess.FILE_NAMES[file_index]}线形成三叠兵",
                              [f"同线兵数={count}"], squares=[chess.square_name(sq) for sq in pawns if chess.square_file(sq) == file_index])
            passed = {sq for sq in pawns if _is_passed(board, sq, color)}
            for square in pawns:
                name = chess.square_name(square)
                file_index = chess.square_file(square)
                adjacent = {file_index - 1, file_index + 1} & set(range(8))
                if not any(chess.square_file(other) in adjacent for other in pawns):
                    self._add(signals, "pawn.isolated", side, f"{name}兵是孤兵",
                              ["相邻兵线没有本方兵"], squares=[name])
                    if file_index == 3:
                        self._add(signals, "pawn.iqp", side, f"{name}是孤立后兵",
                                  ["该兵位于d线且相邻兵线没有本方兵"], squares=[name])
                if square in passed:
                    self._add(signals, "pawn.passed", side, f"{name}是通路兵",
                              ["前方同线及相邻线没有敌兵"], squares=[name])
                    connected = [other for other in passed if other != square and abs(chess.square_file(other) - file_index) == 1 and abs(chess.square_rank(other) - chess.square_rank(square)) <= 1]
                    if connected:
                        self._add(signals, "pawn.connected_passed", side, f"{name}与相邻通路兵相连",
                                  [f"相连兵: {', '.join(chess.square_name(sq) for sq in connected)}"], squares=[name, *(chess.square_name(sq) for sq in connected)])
                    if _attacked_by_pawn(board, color, square):
                        self._add(signals, "pawn.protected_passed", side, f"{name}是受兵保护的通路兵",
                                  ["python-chess验证该格由本方兵攻击"], squares=[name])
                advance = square + (8 if color else -8)
                if 0 <= advance < 64 and board.piece_at(advance) is None and _attacked_by_pawn(board, not color, advance) and not any(
                    chess.square_file(other) in adjacent and _is_behind(other, square, color) for other in pawns
                ):
                    self._add(signals, "pawn.backward", side, f"{name}兵缺少相邻兵支援且推进格受敌兵控制",
                              [f"推进格{chess.square_name(advance)}受敌兵攻击"], squares=[name, chess.square_name(advance)])
                if _pawn_lever(board, square, color):
                    self._add(signals, "pawn.lever", side, f"{name}兵存在直接兵接触或突破点",
                              ["合法推进后可由兵攻击敌兵或当前直接攻击敌兵"], squares=[name])
            chain = [sq for sq in pawns if _attacked_by_pawn(board, color, sq)]
            if len(chain) >= 3:
                self._add(signals, "pawn.chain", side, f"至少{len(chain)}枚兵由本方兵相互连接",
                          [f"兵链格: {', '.join(chess.square_name(sq) for sq in chain)}"], squares=[chess.square_name(sq) for sq in chain])
            advanced = [sq for sq in chain if chess.square_rank(sq) >= 4] if color else [sq for sq in chain if chess.square_rank(sq) <= 3]
            if len(advanced) >= 2:
                self._add(signals, "pawn.space_chain", side, "多枚前伸兵相互支援并限制空间",
                          [f"前伸兵链: {', '.join(chess.square_name(sq) for sq in advanced)}"], squares=[chess.square_name(sq) for sq in advanced])
            for wing, files, rule_id in (("后翼", range(0, 4), "pawn.queenside_majority"), ("王翼", range(4, 8), "pawn.kingside_majority")):
                own = sum(file_counts[file] for file in files)
                enemy = sum(len(board.pieces(chess.PAWN, not color) & chess.BB_FILES[file]) for file in files)
                if own > enemy:
                    self._add(signals, rule_id, side, f"{wing}本方兵数{own}比对方{enemy}多",
                              [f"{wing}兵数差={own - enemy}"])

    def _piece_coordination(self, board: chess.Board, signals: list[ChessReasoningSignal]) -> None:
        open_files = {file for file in range(8) if not any(board.pieces(chess.PAWN, color) & chess.BB_FILES[file] for color in chess.COLORS)}
        for color in chess.COLORS:
            side = _side(color)
            minor_mobility: list[tuple[int, int, chess.Piece]] = []
            bishops = board.pieces(chess.BISHOP, color)
            if len(bishops) >= 2:
                self._add(signals, "piece.bishop_pair", side, "本方保有双象",
                          [f"象所在格: {', '.join(chess.square_name(sq) for sq in bishops)}"], squares=[chess.square_name(sq) for sq in bishops])
            undeveloped = []
            for square, piece in board.piece_map().items():
                if piece.color != color or piece.piece_type == chess.KING:
                    continue
                name = chess.square_name(square)
                attackers = len(board.attackers(not color, square))
                defenders = len(board.attackers(color, square))
                if defenders == 0:
                    self._add(signals, "piece.undefended", side, f"{name}{PIECE_NAMES[piece.piece_type]}当前没有本方保护",
                              [f"本方保护者=0"], squares=[name])
                if attackers > defenders:
                    self._add(signals, "piece.underprotected", side, f"{name}{PIECE_NAMES[piece.piece_type]}受攻次数多于保护次数",
                              [f"攻击者={attackers}，保护者={defenders}"], squares=[name])
                mobility = len([sq for sq in board.attacks(square) if board.color_at(sq) != color])
                if mobility >= 7:
                    self._add(signals, "piece.active", side, f"{name}{PIECE_NAMES[piece.piece_type]}作用到{mobility}个非己方占据格",
                              [f"作用格数量={mobility}"], squares=[name])
                elif piece.piece_type in {chess.KNIGHT, chess.BISHOP, chess.ROOK} and mobility <= 2:
                    self._add(signals, "piece.constrained", side, f"{name}{PIECE_NAMES[piece.piece_type]}当前活动范围较小",
                              [f"作用格数量={mobility}"], squares=[name])
                if piece.piece_type in {chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN}:
                    minor_mobility.append((mobility, square, piece))
                if piece.piece_type == chess.KNIGHT and chess.square_file(square) in {0, 7}:
                    self._add(signals, "piece.knight_rim", side, f"{name}马位于边线",
                              [f"{name}属于a线或h线"], squares=[name])
                if piece.piece_type in {chess.KNIGHT, chess.BISHOP} and _is_outpost(board, square, color):
                    self._add(signals, "piece.outpost", side, f"{name}{PIECE_NAMES[piece.piece_type]}位于有兵保护且不受敌兵攻击的前哨格",
                              ["由本方兵保护且不受敌兵攻击"], squares=[name])
                if piece.piece_type == chess.ROOK:
                    file_index = chess.square_file(square)
                    own_pawns = board.pieces(chess.PAWN, color) & chess.BB_FILES[file_index]
                    enemy_pawns = board.pieces(chess.PAWN, not color) & chess.BB_FILES[file_index]
                    if file_index in open_files:
                        self._add(signals, "piece.rook_open_file", side, f"{name}车占据开放线",
                                  [f"{chess.FILE_NAMES[file_index]}线双方均无兵"], squares=[name])
                    elif not own_pawns and enemy_pawns:
                        self._add(signals, "piece.rook_semi_open", side, f"{name}车占据半开放线",
                                  [f"{chess.FILE_NAMES[file_index]}线无本方兵且有敌兵"], squares=[name])
                    target_rank = 6 if color else 1
                    if chess.square_rank(square) == target_rank:
                        self._add(signals, "piece.rook_seventh", side, f"{name}车侵入对方第二横线",
                                  [f"车位于目标横线{target_rank + 1}"], squares=[name])
                if square in _initial_minor_squares(color) and piece.piece_type in {chess.KNIGHT, chess.BISHOP}:
                    undeveloped.append(name)
            if len(undeveloped) >= 2 and sum(len(board.pieces(pt, c)) for pt in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT) for c in chess.COLORS) >= 10:
                self._add(signals, "piece.development_lag", side, f"仍有{len(undeveloped)}枚轻子停在初始格",
                          [f"初始格轻子: {', '.join(undeveloped)}"], squares=undeveloped)
            if minor_mobility:
                mobility, square, piece = min(minor_mobility, key=lambda item: (item[0], item[1]))
                if mobility <= 3:
                    self._add(signals, "piece.worst_piece", side, f"{chess.square_name(square)}{PIECE_NAMES[piece.piece_type]}是活动最差棋子候选",
                              [f"本方非兵子最低作用格数量={mobility}"], squares=[chess.square_name(square)])
            rooks = list(board.pieces(chess.ROOK, color))
            if len(rooks) >= 2 and _same_clear_line(board, rooks[0], rooks[1]):
                self._add(signals, "piece.connected_rooks", side, "本方两辆车已经联通",
                          [f"车位于{chess.square_name(rooks[0])}和{chess.square_name(rooks[1])}且中间无子"], squares=[chess.square_name(sq) for sq in rooks])
            for square in bishops:
                bishop_color = bool(chess.BB_LIGHT_SQUARES & chess.BB_SQUARES[square])
                same_color_pawns = sum(bool(chess.BB_LIGHT_SQUARES & chess.BB_SQUARES[pawn]) == bishop_color for pawn in board.pieces(chess.PAWN, color))
                mobility = len([sq for sq in board.attacks(square) if board.color_at(sq) != color])
                if same_color_pawns >= 4 and mobility <= 4:
                    self._add(signals, "piece.bad_bishop", side, f"{chess.square_name(square)}象被同色兵链限制",
                              [f"同色本方兵={same_color_pawns}，象作用格={mobility}"], squares=[chess.square_name(square)])

    def _space_and_lines(self, board: chess.Board, signals: list[ChessReasoningSignal]) -> None:
        center = {chess.D4, chess.E4, chess.D5, chess.E5}
        control = {color: sum(bool(board.attackers(color, sq)) for sq in center) for color in chess.COLORS}
        if control[chess.WHITE] != control[chess.BLACK]:
            color = chess.WHITE if control[chess.WHITE] > control[chess.BLACK] else chess.BLACK
            self._add(signals, "space.center_control", _side(color), f"本方控制{control[color]}/4个中心格，多于对方{control[not color]}/4",
                      ["按python-chess攻击者计算d4、e4、d5、e5"])
        safe_space = {}
        for color in chess.COLORS:
            enemy_half = range(32, 64) if color else range(0, 32)
            safe_space[color] = sum(board.is_attacked_by(color, sq) and not _attacked_by_pawn(board, not color, sq) for sq in enemy_half)
        if abs(safe_space[chess.WHITE] - safe_space[chess.BLACK]) >= 5:
            color = chess.WHITE if safe_space[chess.WHITE] > safe_space[chess.BLACK] else chess.BLACK
            self._add(signals, "space.advantage", _side(color), "本方在对方半场控制更多不受敌兵控制的格子",
                      [f"白方安全空间格={safe_space[chess.WHITE]}，黑方={safe_space[chess.BLACK]}"])
        for square, piece in board.piece_map().items():
            if piece.piece_type not in {chess.BISHOP, chess.QUEEN}:
                continue
            diagonal = [sq for sq in board.attacks(square) if abs(chess.square_file(sq) - chess.square_file(square)) == abs(chess.square_rank(sq) - chess.square_rank(square))]
            if len(diagonal) >= 5:
                self._add(signals, "line.open_diagonal", _side(piece.color), f"{chess.square_name(square)}{PIECE_NAMES[piece.piece_type]}拥有较长开放斜线",
                          [f"可直接作用的斜线格={len(diagonal)}"], squares=[chess.square_name(square)])
        constrained = {color: 0 for color in chess.COLORS}
        for square, piece in board.piece_map().items():
            if piece.piece_type in {chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN}:
                if len([sq for sq in board.attacks(square) if board.color_at(sq) != piece.color]) <= 2:
                    constrained[piece.color] += 1
        for color in chess.COLORS:
            if constrained[not color] >= 3 and constrained[not color] >= constrained[color] + 2:
                self._add(signals, "space.restriction", _side(color), f"对方有{constrained[not color]}枚非兵棋子活动受限",
                          [f"本方受限子={constrained[color]}，对方={constrained[not color]}"])

    def _engine_packages(
        self,
        fact_package: "ChessFactPackage | None",
        threat_package: "ThreatPackage | None",
        plan_package: "StrategicPlanPackage | None",
        signals: list[ChessReasoningSignal],
    ) -> None:
        if threat_package is not None:
            for threat in threat_package.threats:
                mapping = {
                    "mate_threat": "king.mate_threat",
                    "promotion_threat": "tactic.promotion",
                    "prepared_tactic": "tactic.double_attack",
                    "tactical_capture": "evaluation.tactical_source",
                    "material_win": "evaluation.tactical_source",
                }
                rule_id = mapping.get(threat.type)
                if rule_id is None:
                    continue
                scope: SignalScope = "current_position" if threat.scope in {"current_direct_threat", "prepared_threat"} else "candidate_route"
                self._add(signals, rule_id, threat.side, threat.evidence[0], list(threat.evidence),
                          moves=threat.supporting_moves, scope=scope, confidence=threat.confidence)
        if plan_package is not None:
            mapping = {
                "improve_worst_piece": "plan.improve_worst_piece",
                "prepare_center_break": "plan.center_break",
                "occupy_open_file": "plan.occupy_open_file",
                "activate_rook": "plan.activate_rook",
                "improve_king_safety": "plan.king_safety",
                "attack_weak_pawn": "plan.attack_weak_pawn",
                "create_passed_pawn": "plan.create_passed_pawn",
                "simplify_endgame": "plan.simplify",
            }
            for plan in plan_package.plans:
                rule_id = mapping.get(plan.type)
                if rule_id:
                    self._add(signals, rule_id, plan.side, plan.goal, list(plan.structural_evidence),
                              moves=plan.supporting_moves, scope="interpretation", confidence=plan.confidence)
        if fact_package is not None:
            material = _material_difference(chess.Board(fact_package.position.fen))
            if material:
                side = "white" if material > 0 else "black"
                self._add(signals, "evaluation.material_source", side, f"{_side_name(side)}按标准子力价值领先{abs(material)}兵",
                          [f"python-chess盘面物质差白减黑={material}"], scope="interpretation", confidence="high")

    @staticmethod
    def _deduplicate(signals: list[ChessReasoningSignal]) -> list[ChessReasoningSignal]:
        result = {}
        for signal in signals:
            key = (signal.rule_id, signal.side, signal.scope, tuple(signal.squares), tuple(signal.moves), signal.summary)
            result.setdefault(key, signal)
        return sorted(result.values(), key=lambda item: (item.category, item.rule_id, item.side, item.summary))


def _side(color: chess.Color) -> Literal["white", "black"]:
    return "white" if color else "black"


def _side_name(side: str) -> str:
    return "白方" if side == "white" else "黑方"


def _wing(square: chess.Square) -> str:
    file_index = chess.square_file(square)
    return "queenside" if file_index <= 2 else "center" if file_index <= 4 else "kingside"


def _captured_square(board: chess.Board, move: chess.Move) -> chess.Square:
    if board.is_en_passant(move):
        return move.to_square - 8 if board.turn else move.to_square + 8
    return move.to_square


def _important_targets_after_move(board: chess.Board, move: chess.Move) -> list[tuple[str, int]]:
    before_attacked = set()
    mover = board.turn
    for square, piece in board.piece_map().items():
        if piece.color != mover and piece.piece_type != chess.PAWN and board.attackers(mover, square):
            before_attacked.add(square)
    after = board.copy(stack=False)
    after.push(move)
    targets = []
    for square, piece in after.piece_map().items():
        if piece.color == mover or piece.piece_type == chess.PAWN:
            continue
        if after.attackers(mover, square) and square not in before_attacked:
            targets.append((chess.square_name(square), PIECE_VALUES[piece.piece_type]))
    return sorted(targets, key=lambda item: (-item[1], item[0]))


def _legal_piece_mobility(board: chess.Board, square: chess.Square) -> int:
    probe = board.copy(stack=False)
    piece = probe.piece_at(square)
    if piece is None:
        return 0
    if probe.turn != piece.color:
        probe.turn = piece.color
    return sum(move.from_square == square for move in probe.legal_moves)


def _pawn_shield_squares(king: chess.Square, color: chess.Color) -> list[chess.Square]:
    direction = 1 if color else -1
    rank = chess.square_rank(king) + direction
    if rank not in range(8):
        return []
    return [chess.square(file_index, rank) for file_index in range(max(0, chess.square_file(king) - 1), min(7, chess.square_file(king) + 1) + 1)]


def _attacked_by_pawn(board: chess.Board, color: chess.Color, square: chess.Square) -> bool:
    return bool(board.attackers(color, square) & board.pieces(chess.PAWN, color))


def _pawn_islands(file_counts: Counter[int]) -> int:
    files = sorted(file_counts)
    return sum(index == 0 or file > files[index - 1] + 1 for index, file in enumerate(files))


def _is_passed(board: chess.Board, square: chess.Square, color: chess.Color) -> bool:
    file_index = chess.square_file(square)
    rank = chess.square_rank(square)
    enemy_pawns = board.pieces(chess.PAWN, not color)
    for enemy in enemy_pawns:
        if abs(chess.square_file(enemy) - file_index) > 1:
            continue
        if color and chess.square_rank(enemy) > rank:
            return False
        if not color and chess.square_rank(enemy) < rank:
            return False
    return True


def _is_behind(other: chess.Square, square: chess.Square, color: chess.Color) -> bool:
    return chess.square_rank(other) < chess.square_rank(square) if color else chess.square_rank(other) > chess.square_rank(square)


def _pawn_lever(board: chess.Board, square: chess.Square, color: chess.Color) -> bool:
    if any(board.piece_at(target) == chess.Piece(chess.PAWN, not color) for target in board.attacks(square)):
        return True
    advance = square + (8 if color else -8)
    if advance not in range(64) or board.piece_at(advance) is not None:
        return False
    return any(board.piece_at(target) == chess.Piece(chess.PAWN, not color) for target in board.attacks(advance))


def _initial_minor_squares(color: chess.Color) -> set[chess.Square]:
    return {chess.B1, chess.C1, chess.F1, chess.G1} if color else {chess.B8, chess.C8, chess.F8, chess.G8}


def _is_outpost(board: chess.Board, square: chess.Square, color: chess.Color) -> bool:
    rank = chess.square_rank(square)
    advanced = rank >= 3 if color else rank <= 4
    return advanced and _attacked_by_pawn(board, color, square) and not _attacked_by_pawn(board, not color, square)


def _same_clear_line(board: chess.Board, left: chess.Square, right: chess.Square) -> bool:
    if chess.square_file(left) != chess.square_file(right) and chess.square_rank(left) != chess.square_rank(right):
        return False
    between = chess.between(left, right)
    return not bool(between & board.occupied)


def _material_difference(board: chess.Board) -> int:
    return sum(
        PIECE_VALUES[piece_type] * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
        for piece_type in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
    )


def _retrieval_theme(category: RuleCategory, rule_id: str) -> str:
    evaluation_themes = {
        "evaluation.material_source": "conversion_and_compensation",
        "evaluation.tactical_source": "forcing_tactics",
        "evaluation.king_safety_source": "king_attack_and_safety",
        "evaluation.structure_source": "pawn_structure_and_space",
        "evaluation.activity_source": "piece_activity_and_coordination",
    }
    plan_themes = {
        "plan.improve_worst_piece": "piece_activity_and_coordination",
        "plan.center_break": "pawn_structure_and_space",
        "plan.occupy_open_file": "piece_activity_and_coordination",
        "plan.activate_rook": "piece_activity_and_coordination",
        "plan.king_safety": "king_attack_and_safety",
        "plan.attack_weak_pawn": "pawn_structure_and_space",
        "plan.create_passed_pawn": "pawn_structure_and_space",
        "plan.simplify": "conversion_and_compensation",
        "plan.activate_king": "piece_activity_and_coordination",
    }
    if rule_id == "evaluation.initiative_gate":
        return "initiative_gate"
    if rule_id == "plan.prophylaxis":
        return "prophylaxis"
    if rule_id in evaluation_themes:
        return evaluation_themes[rule_id]
    if rule_id in plan_themes:
        return plan_themes[rule_id]
    if category == "tactics":
        return "forcing_tactics"
    if category == "king_safety":
        return "king_attack_and_safety"
    if category == "pawn_structure" or category == "space_and_lines":
        return "pawn_structure_and_space"
    if category == "piece_coordination":
        return "piece_activity_and_coordination"
    if category == "plans":
        return "plans"
    return category
