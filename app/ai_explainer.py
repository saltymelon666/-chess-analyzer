from __future__ import annotations

import httpx

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

直接给出中文分析，不要添加标题。"""

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
                            "content": "你是一位国际象棋大师兼教练，擅长用通俗中文解释引擎分析。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
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

        loss_text = (
            f"{move.centipawn_loss} cp"
            if move.centipawn_loss is not None
            else "将杀局面，不能换算为普通 cp"
        )
        prompt = f"""请只根据下面的 Stockfish 事实，为 4—12 岁孩子解释这一步棋。

当前 FEN（走棋前，最佳走法从这里开始）: {move.before_fen}
实战走完后的 FEN: {move.after_fen}
行棋方: {"白方" if move.side == "white" else "黑方"}
实战走法: {move.notation}
质量等级: {move.quality_symbol} {move.quality_label}
走棋前评价（统一白方视角）: {move.before.evaluation}
走棋后评价（统一白方视角）: {move.after.evaluation}
centipawn loss（当前行棋方视角）: {loss_text}
Stockfish 最佳走法: {move.best_move_san or "未返回"}
Stockfish 主要变化: {" ".join(move.best_pv[:8]) or "未返回完整 PV"}
涉及将杀分数: {"是" if move.mate_involved else "否"}
唯一合法走法: {"是" if move.only_legal_move else "否"}

写成三段简短中文：
1. 先说明这步让局面发生了什么变化；
2. 如果有不同的最佳走法，再说明最佳走法和它为什么更稳妥；如果实战就是最佳走法，就鼓励孩子说明它完成了什么；
3. 最后一段必须以“记住：”开头，给一句容易记住的提示。

要求：语言简单、具体、鼓励；不责怪孩子；不得自行改变最佳走法；不得编造棋子、威胁或战术；不得推荐非法走法；不得说反黑白方。如果 Stockfish 事实不足以证明具体原因，请使用“从引擎分数看”等保守表述。不要添加标题。"""
        content = await self._chat(
            system="你是一位耐心的儿童国际象棋教练，只根据给定的 Stockfish 事实解释走法。",
            prompt=prompt,
            max_tokens=420,
            temperature=0.35,
        )
        return content

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
