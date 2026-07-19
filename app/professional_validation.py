from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .models import MoveReview, ProfessionalAnalysis


LENGTH_RANGES = {
    "simple": (400, 700),
    "normal": (800, 1300),
    "complex": (1400, 2200),
}
VAGUE_PHRASES = ("加强中心", "注意防守", "改善子力", "形成压力", "准备进攻", "局面复杂")


@dataclass(frozen=True)
class ProfessionalValidationContext:
    allowed_evidence_ids: set[str]
    evidence_sides: dict[str, str | None]
    evidence_squares: dict[str, set[str]]
    allowed_squares: set[str]
    allowed_moves: set[str]
    played_moves: set[str]
    played_capture: bool
    played_check: bool
    played_checkmate: bool
    actual_line_moves: set[str]
    actual_first_moves: set[str]
    actual_sequence: list[tuple[str, str]]
    candidate_moves: dict[int, set[str]]
    candidate_first_moves: dict[int, set[str]]
    candidate_sequences: dict[int, list[tuple[str, str]]]
    candidate_evidence_ids: dict[int, set[str]]
    pieces: dict[str, tuple[str, str]]
    actual_evidence_ids: set[str]
    complexity: str
    allows_capture: bool
    allows_check: bool
    allows_checkmate: bool


def build_validation_context(move: MoveReview, complexity: str) -> ProfessionalValidationContext:
    evidence_ids: set[str] = {
        f"evaluation:before:{move.index}",
        f"evaluation:after:{move.index}",
        f"complexity:{move.index}",
        move.played_move.id or f"move:played:{move.index}",
    }
    evidence_sides: dict[str, str | None] = {
        f"evaluation:before:{move.index}": None,
        f"evaluation:after:{move.index}": None,
        f"complexity:{move.index}": None,
        move.played_move.id or f"move:played:{move.index}": move.side,
    }
    evidence_squares: dict[str, set[str]] = {
        f"evaluation:before:{move.index}": set(),
        f"evaluation:after:{move.index}": set(),
        f"complexity:{move.index}": set(),
        move.played_move.id or f"move:played:{move.index}": {
            move.played_move.from_square,
            move.played_move.to_square,
        },
    }

    def add_position(position: Any) -> None:
        material_id = str(position.material.get("id", ""))
        if material_id:
            evidence_ids.add(material_id)
            evidence_sides[material_id] = None
            evidence_squares[material_id] = set()
        for piece in position.pieces:
            piece_id = piece.get("id", "")
            if piece_id:
                evidence_ids.add(piece_id)
                evidence_sides[piece_id] = piece.get("side")
                evidence_squares[piece_id] = {piece.get("square", "")} - {""}
        for group in (
            position.piece_activity,
            position.king_safety,
            position.pawn_structure,
            position.threats,
            position.key_pieces,
        ):
            for fact in group:
                if fact.id:
                    evidence_ids.add(fact.id)
                    evidence_sides[fact.id] = fact.side
                    evidence_squares[fact.id] = set(fact.squares)
        for fact in (*position.immediate_checks, *position.immediate_captures):
            if fact.id:
                evidence_ids.add(fact.id)
                evidence_sides[fact.id] = "white" if fact.piece.startswith("white_") else "black"
                evidence_squares[fact.id] = {fact.from_square, fact.to_square}

    add_position(move.position_facts)
    add_position(move.position_facts_after)
    candidate_moves: dict[int, set[str]] = {}
    candidate_first: dict[int, set[str]] = {}
    candidate_sequences: dict[int, list[tuple[str, str]]] = {}
    candidate_evidence_ids: dict[int, set[str]] = {}
    all_variations = []
    for line in move.candidate_lines:
        evidence_ids.add(line.id)
        evidence_sides[line.id] = None
        evidence_squares[line.id] = {
            square for item in line.moves for square in (item.from_square, item.to_square)
        }
        moves = {value for item in line.moves for value in (item.san, item.uci)}
        candidate_moves[line.rank] = moves
        candidate_first[line.rank] = {line.first_move.san, line.first_move.uci}
        candidate_sequences[line.rank] = [(item.san, item.uci) for item in line.moves]
        candidate_evidence_ids[line.rank] = {line.id, *(item.id for item in line.moves)}
        for item in line.moves:
            evidence_ids.add(item.id)
            evidence_sides[item.id] = item.side
            evidence_squares[item.id] = {item.from_square, item.to_square}
        all_variations.extend(line.moves)
        if line.resulting_position_facts:
            add_position(line.resulting_position_facts)

    actual_moves: set[str] = set()
    actual_first: set[str] = set()
    actual_sequence: list[tuple[str, str]] = []
    actual_evidence_ids: set[str] = set()
    if move.actual_move_line:
        evidence_ids.add(move.actual_move_line.id)
        evidence_sides[move.actual_move_line.id] = None
        evidence_squares[move.actual_move_line.id] = {
            square for item in move.actual_move_line.moves for square in (item.from_square, item.to_square)
        }
        actual_evidence_ids.add(move.actual_move_line.id)
        actual_moves = {
            value for item in move.actual_move_line.moves for value in (item.san, item.uci)
        }
        actual_first = {move.actual_move_line.first_move.san, move.actual_move_line.first_move.uci}
        actual_sequence = [(item.san, item.uci) for item in move.actual_move_line.moves]
        for item in move.actual_move_line.moves:
            evidence_ids.add(item.id)
            evidence_sides[item.id] = item.side
            evidence_squares[item.id] = {item.from_square, item.to_square}
            actual_evidence_ids.add(item.id)
        all_variations.extend(move.actual_move_line.moves)
        if move.actual_move_line.resulting_position_facts:
            add_position(move.actual_move_line.resulting_position_facts)

    pieces = {
        piece["square"]: (piece["side"], piece["piece"])
        for piece in move.position_facts.pieces
    }
    allowed_squares = set(move.allowed_squares) | set(pieces)
    for squares in evidence_squares.values():
        allowed_squares.update(squares)
    for item in all_variations:
        allowed_squares.update((item.from_square, item.to_square))
    immediate_moves = {
        value
        for item in (*move.position_facts.immediate_checks, *move.position_facts.immediate_captures)
        for value in (item.san, item.uci)
    }
    allowed_moves = set(move.allowed_moves) | actual_moves | immediate_moves
    for values in candidate_moves.values():
        allowed_moves.update(values)
    allows_capture = (
        move.played_move.capture
        or bool(move.position_facts.immediate_captures)
        or any(item.capture for item in all_variations)
    )
    allows_checkmate = (
        move.played_move.checkmate
        or any(item.checkmate for item in move.position_facts.immediate_checks)
        or any(item.checkmate for item in all_variations)
    )
    allows_check = (
        allows_checkmate
        or move.played_move.check
        or bool(move.position_facts.immediate_checks)
        or any(item.check for item in all_variations)
    )
    return ProfessionalValidationContext(
        allowed_evidence_ids=evidence_ids,
        evidence_sides=evidence_sides,
        evidence_squares=evidence_squares,
        allowed_squares=allowed_squares,
        allowed_moves=allowed_moves,
        played_moves={move.played_move.san, move.played_move.uci},
        played_capture=move.played_move.capture,
        played_check=move.played_move.check,
        played_checkmate=move.played_move.checkmate,
        actual_line_moves=actual_moves,
        actual_first_moves=actual_first,
        actual_sequence=actual_sequence,
        candidate_moves=candidate_moves,
        candidate_first_moves=candidate_first,
        candidate_sequences=candidate_sequences,
        candidate_evidence_ids=candidate_evidence_ids,
        pieces=pieces,
        actual_evidence_ids=actual_evidence_ids,
        complexity=complexity,
        allows_capture=allows_capture,
        allows_check=allows_check,
        allows_checkmate=allows_checkmate,
    )


def parse_professional_analysis(content: str) -> tuple[ProfessionalAnalysis | None, list[str]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
        return ProfessionalAnalysis.model_validate(payload), []
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return None, [f"专业分析不是规定的JSON结构：{exc}"]


def validate_professional_analysis(
    analysis: ProfessionalAnalysis,
    context: ProfessionalValidationContext,
    *,
    enforce_length: bool = True,
) -> list[str]:
    errors: list[str] = []
    if analysis.complexity != context.complexity:
        errors.append(f"complexity应为{context.complexity}")

    # Dynamic sections are checked against the same deterministic selector used before DeepSeek.
    # The MoveReview is not stored in the context, so scope checks below use route evidence sets;
    # the resolver itself enforces the complete selected-fact allow-list.
    if analysis.position_assessment.material is not None:
        errors.append("positionAssessment.material: 不允许固定展示物质差栏目")

    payload = analysis.model_dump(by_alias=True)
    refs = _collect_key_values(payload, "evidenceRefs")
    invalid_refs = sorted({ref for values in refs for ref in values if ref not in context.allowed_evidence_ids})
    if invalid_refs:
        errors.append("出现不存在的evidenceRefs：" + "、".join(invalid_refs))

    all_text = "\n".join(_all_prose_strings(payload))
    malformed = sorted(set(re.findall(r"(?<![A-Za-z0-9])([A-Za-z][0-9])(?![A-Za-z0-9])", all_text)))
    malformed = [
        item for item in malformed
        if item[0].lower() != "m" and not re.fullmatch(r"[a-h][1-8]", item, re.IGNORECASE)
    ]
    if malformed:
        errors.append("出现棋盘范围外的格子：" + "、".join(malformed))
    squares = {item.lower() for item in re.findall(r"(?<![A-Za-z0-9])([a-h][1-8])(?![A-Za-z0-9])", all_text, re.IGNORECASE)}
    invalid_squares = sorted(squares - {item.lower() for item in context.allowed_squares})
    if invalid_squares:
        errors.append("出现事实包之外的格子：" + "、".join(invalid_squares))
    mentioned_uci = set(
        re.findall(r"(?<![A-Za-z0-9])([a-h][1-8][a-h][1-8][qrbn]?)(?![A-Za-z0-9])", all_text, re.IGNORECASE)
    )
    invalid_uci = sorted(item for item in mentioned_uci if item.lower() not in {move.lower() for move in context.allowed_moves})
    if invalid_uci:
        errors.append("出现Stockfish事实包之外的UCI走法：" + "、".join(invalid_uci))
    san_pattern = r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])"
    mentioned_san = set(re.findall(san_pattern, all_text))
    allowed_san = {_normal_san(move) for move in context.allowed_moves}
    invalid_san = sorted(item for item in mentioned_san if _normal_san(item) not in allowed_san)
    if invalid_san:
        errors.append("出现Stockfish事实包之外的SAN走法：" + "、".join(invalid_san))

    for item in analysis.key_pieces:
        expected = context.pieces.get(item.square)
        if expected != (item.side, item.piece):
            errors.append(f"关键棋子{item.side}_{item.piece}@{item.square}不存在于走棋前FEN")
        if not _refs_support_side(item.evidence_refs, item.side, context):
            errors.append(f"关键棋子{item.square}的证据与颜色不符")
        if not _refs_support_square(item.evidence_refs, item.square, context):
            errors.append(f"关键棋子{item.square}的证据没有指向该格")

    for side, assessment in (
        ("white", analysis.position_assessment.king_safety.white),
        ("black", analysis.position_assessment.king_safety.black),
    ):
        if assessment is None:
            continue
        if not _refs_support_side(assessment.evidence_refs, side, context, allow_neutral=True):
            errors.append(f"{side}王安全结论的证据颜色不符")
    king_safety = analysis.position_assessment.king_safety
    if not king_safety.is_relevant and (king_safety.white is not None or king_safety.black is not None):
        errors.append("kingSafety.isRelevant为false时不得保留王安全说明")
    if king_safety.is_relevant and king_safety.white is None and king_safety.black is None:
        errors.append("kingSafety.isRelevant为true时至少需要一方的具体证据")

    if analysis.played_move_analysis.move not in context.played_moves:
        errors.append("playedMoveAnalysis.move不等于实际走法")
    played_text = " ".join(
        [
            analysis.played_move_analysis.intention,
            *analysis.played_move_analysis.positive_effects,
            *analysis.played_move_analysis.problems,
            analysis.played_move_analysis.evaluation_reason,
        ]
    )
    errors.extend(
        _validate_event_scope(
            played_text,
            capture=context.played_capture,
            check=context.played_check,
            checkmate=context.played_checkmate,
            label="实战走法",
        )
    )
    if context.actual_first_moves and analysis.played_move_analysis.strongest_response not in context.actual_first_moves:
        errors.append("strongestResponse不在实战走法后的Stockfish路线中")
    for phase in analysis.played_move_analysis.continuation_phases:
        errors.extend(_validate_phase_moves(phase.moves, context.actual_line_moves, "实战续算"))
        if phase.moves and not set(phase.evidence_refs).intersection(context.actual_evidence_ids):
            errors.append("实战续算阶段没有引用对应Stockfish路线证据")
    errors.extend(
        _validate_phase_sequence(
            [move for phase in analysis.played_move_analysis.continuation_phases for move in phase.moves],
            context.actual_sequence,
            "实战续算",
        )
    )
    actual_text = " ".join(
        [
            analysis.played_move_analysis.strongest_response,
            analysis.played_move_analysis.resulting_position,
            *[phase.explanation for phase in analysis.played_move_analysis.continuation_phases],
        ]
    )
    errors.extend(
        _validate_event_scope(
            actual_text,
            capture=any("x" in san for san, _ in context.actual_sequence),
            check=any("+" in san or "#" in san for san, _ in context.actual_sequence),
            checkmate=any("#" in san for san, _ in context.actual_sequence),
            label="实战续算路线",
        )
    )

    seen_ranks: set[int] = set()
    if len(analysis.candidate_lines) > len(context.candidate_moves):
        errors.append("生成了不存在的额外候选路线")
    for line in analysis.candidate_lines:
        if line.rank in seen_ranks:
            errors.append(f"候选路线排名{line.rank}重复")
        seen_ranks.add(line.rank)
        if line.rank not in context.candidate_moves:
            errors.append(f"候选路线{line.rank}不存在")
            continue
        if line.first_move not in context.candidate_first_moves[line.rank]:
            errors.append(f"候选路线{line.rank}的firstMove与Stockfish不符")
        route_evidence = context.candidate_evidence_ids[line.rank]
        if not set(line.evidence_refs).intersection(route_evidence):
            errors.append(f"候选路线{line.rank}没有引用自身路线证据")
        for phase in line.continuation_phases:
            errors.extend(_validate_phase_moves(phase.moves, context.candidate_moves[line.rank], f"候选路线{line.rank}"))
            if phase.moves and not set(phase.evidence_refs).intersection(route_evidence):
                errors.append(f"候选路线{line.rank}的阶段没有引用自身PV证据")
        expected_scope = f"candidate_line_{line.rank}"
        for event in line.events:
            if event.scope != expected_scope:
                errors.append(f"候选路线{line.rank}的事件scope串入了其他路线")
            if not set(event.evidence_refs).intersection(route_evidence):
                errors.append(f"候选路线{line.rank}的内部事件没有引用自身PV证据")
        errors.extend(
            _validate_phase_sequence(
                [move for phase in line.continuation_phases for move in phase.moves],
                context.candidate_sequences[line.rank],
                f"候选路线{line.rank}",
            )
        )
        if line.strategy_tags and not line.evidence_refs:
            errors.append(f"候选路线{line.rank}的战略方向没有证据")
        line_text = " ".join(
            [line.direct_purpose, line.opponent_response, line.resulting_position, *line.advantages, *line.risks]
            + [phase.explanation for phase in line.continuation_phases]
        )
        route_pairs = context.candidate_sequences[line.rank]
        errors.extend(
            _validate_event_scope(
                line_text,
                capture=any("x" in san for san, _ in route_pairs),
                check=any("+" in san or "#" in san for san, _ in route_pairs),
                checkmate=any("#" in san for san, _ in route_pairs),
                label=f"候选路线{line.rank}",
            )
        )
    if seen_ranks != set(context.candidate_moves):
        errors.append("候选路线数量或排名与Stockfish不一致")

    for side, plans in (("white", analysis.plans.white), ("black", analysis.plans.black)):
        if not plans:
            errors.append(f"{side}没有给出带证据的计划或明确的证据不足说明")
        for plan in plans:
            if not plan.evidence_refs:
                errors.append(f"{side}的战略方向没有证据")
            elif not _refs_support_side(plan.evidence_refs, side, context, allow_neutral=True):
                errors.append(f"{side}计划只引用了对方证据")

    for side, weaknesses in (("white", analysis.weaknesses.white), ("black", analysis.weaknesses.black)):
        for weakness in weaknesses:
            if not _refs_support_side(weakness.evidence_refs, side, context):
                errors.append(f"{side}弱点的证据颜色不符")

    for threat in analysis.threats:
        if threat.scope != "current_position":
            errors.append("全局潜在威胁只能使用current_position scope")
        if not _refs_support_side(threat.evidence_refs, threat.side, context, allow_neutral=True):
            errors.append(f"{threat.side}威胁的证据颜色不符")
        target_squares = _mentioned_squares(threat.target)
        if target_squares and not _refs_support_any_square(threat.evidence_refs, target_squares, context):
            errors.append(f"{threat.side}威胁目标没有对应证据")

    for side in ("white", "black"):
        count = sum(1 for item in analysis.key_pieces if item.side == side)
        if not 1 <= count <= 3:
            errors.append(f"{side}关键棋子数量应为1—3枚，实际{count}枚")

    danger = analysis.main_danger
    if danger.side_in_danger != "none":
        danger_squares = _mentioned_squares(danger.description)
        has_concrete_square = bool(danger_squares)
        has_piece = bool(re.search(r"(?:白|黑)(?:兵|马|象|车|后|王)|(?:pawn|knight|bishop|rook|queen|king)", danger.description, re.IGNORECASE))
        if not (has_concrete_square and has_piece):
            errors.append("mainDanger.description: 没有同时指出具体棋子和格子")
        if len(danger_squares) < 2:
            errors.append("mainDanger.description: 没有同时指出来源格和目标格")
        if danger_squares and not _refs_support_any_square(danger.evidence_refs, danger_squares, context):
            errors.append("mainDanger.evidenceRefs: 提到的格子没有对应证据")
        if len(danger.consequence.strip()) < 6:
            errors.append("mainDanger.consequence: 没有说明不处理的后果")
    if not danger.evidence_refs:
        errors.append("mainDanger.evidenceRefs: 没有证据")

    for phrase in VAGUE_PHRASES:
        found = False
        for path, value in _all_strings_with_paths(payload):
            for sentence in re.findall(r"[^。！？\n]*" + re.escape(phrase) + r"[^。！？\n]*", value):
                if not _is_concrete(sentence, context):
                    errors.append(f"{path}: 空泛结论“{phrase}”缺少棋子、格子或路线说明")
                    found = True
                    break
            if found:
                break

    for path, value in _all_strings_with_paths(payload):
        positive_text = _remove_negated_events(value)
        if re.search(r"吃子|吃掉|捕获|拿掉", positive_text) and not context.allows_capture:
            errors.append(f"{path}: 描述了结构化数据中不存在的吃子")
        if re.search(r"将杀|绝杀", positive_text) and not context.allows_checkmate:
            errors.append(f"{path}: 描述了结构化数据中不存在的将杀")
        if "将军" in positive_text and not context.allows_check:
            errors.append(f"{path}: 描述了结构化数据中不存在的将军")

    if enforce_length:
        length = _narrative_length(payload)
        minimum, maximum = LENGTH_RANGES[context.complexity]
        if not minimum <= length <= maximum:
            errors.append(f"专业分析正文长度应为{minimum}—{maximum}字，实际{length}字")
    return errors


def _collect_key_values(value: Any, key: str) -> list[list[str]]:
    found: list[list[str]] = []
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if current_key == key and isinstance(current_value, list):
                found.append([str(item) for item in current_value])
            else:
                found.extend(_collect_key_values(current_value, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_key_values(item, key))
    return found


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _all_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _all_strings(child)]
    return []


def _narrative_length(payload: dict[str, Any]) -> int:
    ignored = {"evidenceRefs", "complexity", "strategyTag", "strategyTags", "side", "level", "rank", "errorType"}

    def collect(value: Any, key: str = "") -> list[str]:
        if key in ignored:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [text for child_key, child in value.items() for text in collect(child, child_key)]
        if isinstance(value, list):
            return [text for child in value for text in collect(child, key)]
        return []

    return len(re.sub(r"\s+", "", "".join(collect(payload))))


def _validate_phase_moves(moves: list[str], allowed: set[str], label: str) -> list[str]:
    invalid = sorted({move for move in moves if move not in allowed})
    return [f"{label}引用了Stockfish PV之外的走法：{'、'.join(invalid)}"] if invalid else []


def _validate_phase_sequence(
    moves: list[str],
    expected: list[tuple[str, str]],
    label: str,
) -> list[str]:
    if not expected:
        return [] if not moves else [f"{label}在空PV中补写了走法"]
    if len(moves) != len(expected):
        return [f"{label}没有完整覆盖Stockfish提供的{len(expected)}个半回合"]
    for index, (move, choices) in enumerate(zip(moves, expected), start=1):
        if move not in choices:
            return [f"{label}第{index}个半回合与对应PV顺序不符"]
    return []


def _refs_support_side(
    refs: list[str],
    side: str,
    context: ProfessionalValidationContext,
    *,
    allow_neutral: bool = False,
) -> bool:
    sides = {context.evidence_sides.get(ref) for ref in refs}
    if side in sides:
        return True
    return allow_neutral and None in sides and (not ({"white", "black"} - {side}).intersection(sides))


def _refs_support_square(
    refs: list[str],
    square: str,
    context: ProfessionalValidationContext,
) -> bool:
    normalized = square.lower()
    return any(normalized in {item.lower() for item in context.evidence_squares.get(ref, set())} for ref in refs)


def _refs_support_any_square(
    refs: list[str],
    squares: set[str],
    context: ProfessionalValidationContext,
) -> bool:
    supported = {
        square.lower()
        for ref in refs
        for square in context.evidence_squares.get(ref, set())
    }
    return bool({square.lower() for square in squares}.intersection(supported))


def _mentioned_squares(text: str) -> set[str]:
    return {
        item.lower()
        for item in re.findall(r"(?<![A-Za-z0-9])([a-h][1-8])(?![A-Za-z0-9])", text, re.IGNORECASE)
    }


def _is_concrete(sentence: str, context: ProfessionalValidationContext) -> bool:
    has_square = bool(re.search(r"(?<![A-Za-z0-9])[a-h][1-8](?![A-Za-z0-9])", sentence, re.IGNORECASE))
    has_move = any(move and move in sentence for move in context.allowed_moves)
    has_piece = bool(re.search(r"(?:白|黑)?(?:兵|马|象|车|后|王)|pawn|knight|bishop|rook|queen|king", sentence, re.IGNORECASE))
    return (has_square and has_piece) or has_move


def _all_strings_with_paths(value: Any, path: str = "$") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, str):
        result.append((path, value))
    elif isinstance(value, dict):
        for key, child in value.items():
            result.extend(_all_strings_with_paths(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(_all_strings_with_paths(child, f"{path}[{index}]"))
    return result


def _all_prose_strings(value: Any, key: str = "") -> list[str]:
    if key == "evidenceRefs":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for child_key, child in value.items()
            for text in _all_prose_strings(child, child_key)
        ]
    if isinstance(value, list):
        return [text for child in value for text in _all_prose_strings(child, key)]
    return []


def _remove_negated_events(text: str) -> str:
    event = r"(?:吃子|吃掉|捕获|拿掉|将军|将杀|绝杀)"
    return re.sub(rf"(?:不是|没有|并未|不能|无法)[^。！？\n]{{0,12}}{event}", "", text)


def _normal_san(value: str) -> str:
    return value.replace("0", "O").rstrip("+#")


def _validate_event_scope(
    text: str,
    *,
    capture: bool,
    check: bool,
    checkmate: bool,
    label: str,
) -> list[str]:
    positive = _remove_negated_events(text)
    errors = []
    if re.search(r"吃子|吃掉|捕获|拿掉", positive) and not capture:
        errors.append(f"{label}被描述成了未验证的吃子")
    if re.search(r"将杀|绝杀", positive) and not checkmate:
        errors.append(f"{label}被描述成了未验证的将杀")
    if "将军" in positive and not check:
        errors.append(f"{label}被描述成了未验证的将军")
    return errors
