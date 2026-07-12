from __future__ import annotations

import httpx

from .models import EngineResult


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

    @staticmethod
    def _score_text(centipawn: int | None, mate_in: int | None) -> str:
        if mate_in is not None:
            return f"白 M{mate_in}" if mate_in > 0 else f"黑 M{abs(mate_in)}"
        return f"{(centipawn or 0) / 100:+.2f}"

