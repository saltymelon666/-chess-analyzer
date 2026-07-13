from __future__ import annotations

import json
import re

import httpx

from .complexity import EXPLANATION_PROFILES
from .models import EngineResult, MoveReview


class DeepSeekExplainer:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def explain(self, fen: str, result: EngineResult) -> str:
        if not self.configured:
            raise RuntimeError("服务端尚未配置 DeepSeek API Key")

        moves_info = "\n".join(
            f"#{index}: {move.san} ({self._score_text(move.centipawn, move.mate_in)}) "
            f"后续: {' '.join(move.pv[:5]) or '-'}"
            for index, move in enumerate(result.top_moves, start=1)
        )
        prompt = f"""你是一个国际象棋分析助手。请用简洁的中文分析以下局面：

局面 FEN: {fen}
引擎评估（统一为白方视角）: {result.evaluation}
搜索深度: {result.depth} 层
最佳着法及后续变化:
{moves_info}

请用 2-4 句话说明：
1. 当前局面的整体评价，以及谁占优；
2. 最佳着法的主要意图；
3. 给当前行棋方的简短建议。

直接给出中文分析，不要添加标题。不得补充候选走法和 PV 中没有出现的具体走法或格子；证据不足时只描述引擎分数。"""

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你不是国际象棋引擎，不得自行计算或猜测棋局。"
                                "只能用输入中的 FEN、评价、候选走法和主要变化做保守解释。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            data = response.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("DeepSeek 返回了空内容")
        return content

    async def explain_move(self, move: MoveReview) -> str:
        if not self.configured:
            raise RuntimeError("服务端尚未配置 DeepSeek API Key")

        profile = EXPLANATION_PROFILES[move.complexity]
        prompt = self._move_prompt(move, profile)
        system = (
            "你是一位耐心的儿童国际象棋教练。你不是国际象棋引擎，不得自行计算或猜测棋局。"
            "你只能根据输入中已经确认的结构化棋局事实进行解释。不得提及输入中不存在的棋子、格子、"
            "走法、吃子、将军和战术。如果证据不足，请使用保守表达，不能编造原因。"
        )
        content = await self._chat(
            system=system,
            prompt=prompt,
            max_tokens=int(profile["max_tokens"]),
            temperature=0.2,
        )
        errors = validate_move_explanation(content, move)
        if not errors:
            return content

        correction = (
            prompt
            + "\n\n上一次回答未通过事实校验，原因："
            + "；".join(errors)
            + f"\n上一次回答：{content}\n请只用允许的数据重写一次。"
        )
        corrected = await self._chat(
            system=system,
            prompt=correction,
            max_tokens=int(profile["max_tokens"]),
            temperature=0.1,
        )
        if not validate_move_explanation(corrected, move):
            return corrected
        return conservative_move_explanation(move)

    @staticmethod
    def _move_prompt(move: MoveReview, profile: dict[str, int]) -> str:
        payload = {
            "fullMoveNumber": move.move_number,
            "side": move.side,
            "fenBefore": move.before_fen,
            "fenAfter": move.after_fen,
            "playedMove": move.played_move.model_dump(by_alias=True),
            "bestMove": move.best_move.model_dump(by_alias=True) if move.best_move else None,
            "opponentReplyAfterPlayedMove": move.opponent_reply.model_dump(by_alias=True) if move.opponent_reply else None,
            "evaluationBefore": move.before.model_dump(),
            "evaluationAfter": move.after.model_dump(),
            "centipawnLoss": move.centipawn_loss,
            "quality": {
                "symbol": move.quality_symbol,
                "label": move.quality_label,
            },
            "principalVariation": move.principal_variation,
            "principalVariationFacts": [fact.model_dump() for fact in move.principal_variation_facts],
            "opponentVariation": move.opponent_variation,
            "opponentVariationFacts": [fact.model_dump() for fact in move.opponent_variation_facts],
            "complexity": move.complexity,
            "complexityFactors": move.complexity_factors.model_dump(),
            "verifiedFacts": move.verified_facts,
            "allowedSquares": move.allowed_squares,
            "allowedMoves": move.allowed_moves,
            "piecesBefore": move.pieces_before,
        }
        format_rule = {
            "simple": "用两小段：说明实战走法和等级；给一句记忆提示。",
            "normal": "用三小段：说明实战变化；比较合法推荐；给一句记忆提示。",
            "complex": (
                "用四小段：说明实战变化；只根据 opponentReplyAfterPlayedMove 说明对手下一步的引擎选择；"
                "比较最佳走法并原样概括主要变化；给一句记忆提示。"
            ),
        }[move.complexity]
        return f"""请把下面已经由 python-chess 和 Stockfish 确认的数据，改写成适合 4—12 岁孩子的中文。

结构化事实：
{json.dumps(payload, ensure_ascii=False, indent=2)}

本局面复杂度是 {move.complexity}。正文必须为 {profile['min_chars']}—{profile['max_chars']} 个非空白字符。
{format_rule}最后一句必须以“记住：”开头。
只有 verifiedFacts 可以作为具体棋理原因。PV 只能按原样介绍为“可能的主要变化”，不能把它扩写成未提供的威胁。
不得输出 allowedSquares 之外的格子，不得输出 allowedMoves 之外的具体走法。不要添加标题，不要责怪孩子。"""

    async def _chat(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("DeepSeek 返回了空内容")
        return content

    @staticmethod
    def _score_text(centipawn: int | None, mate_in: int | None) -> str:
        if mate_in is not None:
            return f"白 M{mate_in}" if mate_in > 0 else f"黑 M{abs(mate_in)}"
        return f"{(centipawn or 0) / 100:+.2f}"


def validate_move_explanation(content: str, move: MoveReview) -> list[str]:
    errors: list[str] = []
    compact_length = len(re.sub(r"\s+", "", content))
    profile = EXPLANATION_PROFILES[move.complexity]
    if compact_length < profile["min_chars"] or compact_length > profile["max_chars"]:
        errors.append(
            f"长度应为{profile['min_chars']}—{profile['max_chars']}字，实际{compact_length}字"
        )

    allowed_squares = set(move.allowed_squares)
    square_like = set(re.findall(r"(?<![a-z0-9])([a-z][0-9])(?![a-z0-9])", content, re.IGNORECASE))
    out_of_range = sorted(
        value for value in square_like if not re.fullmatch(r"[a-h][1-8]", value, re.IGNORECASE)
    )
    if out_of_range:
        errors.append("出现棋盘范围外的格子：" + "、".join(out_of_range))
    mentioned_squares = set(re.findall(r"(?<![a-z0-9])[a-h][1-8](?![a-z0-9])", content, re.IGNORECASE))
    invalid_squares = sorted(square.lower() for square in mentioned_squares if square.lower() not in allowed_squares)
    if invalid_squares:
        errors.append("出现未提供的格子：" + "、".join(invalid_squares))

    allowed_uci = {value.lower() for value in move.allowed_moves if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", value)}
    mentioned_uci = set(re.findall(r"(?<![a-z0-9])([a-h][1-8][a-h][1-8][qrbn]?)(?![a-z0-9])", content, re.IGNORECASE))
    invalid_uci = sorted(value for value in mentioned_uci if value.lower() not in allowed_uci)
    if invalid_uci:
        errors.append("出现未提供的 UCI 走法：" + "、".join(invalid_uci))

    allowed_san = {_normal_san(value) for value in move.allowed_moves if not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", value)}
    san_pattern = r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][18]=[QRBN])[+#]?(?![A-Za-z0-9])"
    mentioned_san = set(re.findall(san_pattern, content))
    invalid_san = sorted(value for value in mentioned_san if _normal_san(value) not in allowed_san)
    if invalid_san:
        errors.append("出现未提供的 SAN 走法：" + "、".join(invalid_san))

    all_moves = [
        move.played_move,
        *([move.best_move] if move.best_move else []),
        *([move.opponent_reply] if move.opponent_reply else []),
        *move.principal_variation_facts,
        *move.opponent_variation_facts,
    ]
    pv_text = " ".join([*move.principal_variation, *move.opponent_variation])
    allows_capture = any(item.capture for item in all_moves) or "x" in pv_text
    allows_checkmate = any(item.checkmate for item in all_moves) or "#" in pv_text or move.mate_involved
    allows_check = allows_checkmate or any(item.check for item in all_moves) or "+" in pv_text
    allows_castling = any(item.castling for item in all_moves) or "O-O" in pv_text
    allows_promotion = any(item.promotion for item in all_moves) or "=" in pv_text
    event_term = r"(?:吃子|吃掉|捕获|拿掉|将军|将杀|绝杀|易位|升变)"
    positive_claims = re.sub(
        rf"(?:不是|没有|并未|并没有|未)(?:形成|完成|发生|造成)?{event_term}(?:、{event_term})*",
        "",
        content,
    )
    if re.search(r"吃子|吃掉|捕获|拿掉", positive_claims) and not allows_capture:
        errors.append("把普通走法描述成了吃子")
    if re.search(r"将杀|绝杀", positive_claims) and not allows_checkmate:
        errors.append("提到了未确认的将杀")
    if "将军" in positive_claims and not allows_check:
        errors.append("提到了未确认的将军")
    if "易位" in positive_claims and not allows_castling:
        errors.append("提到了未确认的易位")
    if re.search(r"升变|变后", positive_claims) and not allows_promotion:
        errors.append("提到了未确认的升变")

    played_scope = " ".join(re.findall(r"(?:实战|这一步|这步)[^。！？\n]{0,36}", positive_claims))
    if re.search(r"吃子|吃掉|捕获|拿掉", played_scope) and not move.played_move.capture:
        errors.append("把实战普通走法描述成了吃子")
    if re.search(r"将杀|绝杀", played_scope) and not move.played_move.checkmate:
        errors.append("把实战走法描述成了将杀")
    if "将军" in played_scope and not move.played_move.check:
        errors.append("把实战走法描述成了将军")
    if "易位" in played_scope and not move.played_move.castling:
        errors.append("把实战走法描述成了易位")
    if re.search(r"升变|变后", played_scope) and not move.played_move.promotion:
        errors.append("把实战走法描述成了升变")

    piece_name_map = {"兵": "pawn", "卒": "pawn", "马": "knight", "象": "bishop", "车": "rook", "后": "queen", "王": "king"}
    move_claim_pattern = r"(白|黑)(?:方的)?(兵|卒|马|象|车|后|王)[^。！？\n]{0,12}?从\s*([a-h][1-8])[^。！？\n]{0,8}?到\s*([a-h][1-8])"
    for color_zh, piece_zh, from_square, to_square in re.findall(move_claim_pattern, content):
        piece_id = f"{'white' if color_zh == '白' else 'black'}_{piece_name_map[piece_zh]}"
        if not any(
            item.piece == piece_id and item.from_square == from_square and item.to_square == to_square
            for item in all_moves
        ):
            errors.append(f"{color_zh}{piece_zh}的起点、终点或颜色与结构化事实不符")

    inventory = set(move.pieces_before.values())
    piece_terms = {
        "pawn": ("兵", "卒"),
        "knight": ("马",),
        "bishop": ("象",),
        "rook": ("车",),
        "queen": ("后",),
        "king": ("王",),
    }
    for color, prefix in (("white", "白"), ("black", "黑")):
        for piece, terms in piece_terms.items():
            patterns = [prefix + term for term in terms] + [prefix + "方的" + term for term in terms]
            if any(pattern in content for pattern in patterns) and f"{color}_{piece}" not in inventory:
                errors.append(f"提到了当前局面不存在的{prefix}{terms[0]}")
    if re.search(r"皇后|王后|棋后", content) and not any(item.endswith("_queen") for item in inventory):
        errors.append("提到了当前局面不存在的后")

    if "记住：" not in content:
        errors.append("缺少以“记住：”开头的提示")
    return errors


def conservative_move_explanation(move: MoveReview) -> str:
    side = "白方" if move.side == "white" else "黑方"
    played = move.played_move
    best = move.best_move
    event = ""
    if played.capture:
        event = "，棋规确认它完成了吃子"
    if played.castling:
        event = "，棋规确认它完成了王车易位"
    if played.promotion:
        event = "，棋规确认它完成了升变"
    if played.checkmate:
        event = "，棋规确认它形成将杀"
    elif played.check:
        event = "，棋规确认它形成将军"
    opening = f"第{move.move_number}回合，{side}走了{played.san}（{played.from_square}到{played.to_square}）{event}，被评为{move.quality_label}。"
    tip = "记住：先确认棋盘事实，再比较实战走法和引擎推荐。"
    if move.complexity == "simple":
        return _fit_fallback(opening + tip, move)

    best_text = (
        f"Stockfish更推荐{best.san}（{best.from_square}到{best.to_square}）。"
        if best and best.uci != played.uci
        else "实战走法已经接近Stockfish的第一选择。"
    )
    score_text = f"从引擎分数看，评价由{move.before.evaluation}变为{move.after.evaluation}。"
    if move.complexity == "normal":
        return _fit_fallback(opening + score_text + best_text + tip, move)

    pv_text = (
        "已验证的主要变化是" + "、".join(move.principal_variation[:8]) + "。"
        if move.principal_variation
        else "Stockfish没有返回足够完整的主要变化，因此不猜测具体战术。"
    )
    detail = (
        f"这个局面有{move.complexity_factors.legal_move_count}个合法走法，"
        f"主要变化长度为{move.complexity_factors.pv_length}个半回合。"
    )
    return _fit_fallback(opening + score_text + best_text + detail + pv_text + "没有经过棋规确认的威胁不在这里展开。" + tip, move)


def _fit_fallback(text: str, move: MoveReview) -> str:
    profile = EXPLANATION_PROFILES[move.complexity]
    compact_length = len(re.sub(r"\s+", "", text))
    if compact_length < profile["min_chars"]:
        padding = "这段说明只使用已经核对过的棋盘信息。"
        while len(re.sub(r"\s+", "", text)) < profile["min_chars"]:
            text = text.replace("记住：", padding + "记住：", 1)
    if len(re.sub(r"\s+", "", text)) > profile["max_chars"]:
        # This branch is only a final safety net; keep the verified opening and child-friendly tip.
        text = text[: max(0, profile["max_chars"] - len("记住：先核对棋盘事实。"))] + "记住：先核对棋盘事实。"
    return text


def _normal_san(value: str) -> str:
    return value.replace("0", "O").rstrip("+#")
