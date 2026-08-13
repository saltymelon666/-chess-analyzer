from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Literal, Sequence

import chess
import chess.pgn
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_OPENING_CATALOG = Path(__file__).resolve().parent / "data" / "opening-path-catalog.json"
DEFAULT_CLASSIC_OPENING_EXTENSIONS = (
    Path(__file__).resolve().parent / "data" / "classic-opening-extensions.json"
)
DEFAULT_OPENING_EXPLANATIONS = (
    Path(__file__).resolve().parent / "data" / "opening-explanations.json"
)
OPENING_CONTEXT_RESOLVER_VERSION = "opening-context-2"


class OpeningLookupRequest(BaseModel):
    pgn: str | None = Field(default=None, min_length=1, max_length=100_000)
    fen: str | None = Field(default=None, min_length=15, max_length=120)

    @model_validator(mode="after")
    def require_position_source(self) -> "OpeningLookupRequest":
        if not self.pgn and not self.fen:
            raise ValueError("pgn或fen至少提供一个")
        return self


class OpeningContinuation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    san: str
    uci: str
    opening_name: str = Field(alias="openingName")
    eco: str


class OpeningMatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    opening_id: str = Field(alias="openingId")
    eco: str
    name: str
    family_name: str = Field(alias="familyName")
    variation_path: list[str] = Field(alias="variationPath", default_factory=list)
    pgn: str
    san_moves: list[str] = Field(alias="sanMoves")
    uci_moves: list[str] = Field(alias="uciMoves")
    matched_ply: int = Field(alias="matchedPly", ge=1)


class OpeningHumanExplanation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    text_en: str | None = Field(default=None, alias="textEn")
    text_zh: str | None = Field(default=None, alias="textZh")
    matched_ply: int = Field(alias="matchedPly", ge=1)
    page_title: str = Field(alias="pageTitle")
    page_url: str = Field(alias="pageUrl")
    revision_id: int = Field(alias="revisionId")
    license: str
    attribution: str


class OpeningLookupResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    matched: bool
    match_type: Literal["exact_path", "path_prefix", "position_transposition", "exact_fen", "none"] = Field(
        alias="matchType"
    )
    query_ply: int = Field(alias="queryPly", ge=0)
    current_fen: str = Field(alias="currentFen")
    opening: OpeningMatch | None = None
    human_explanation: OpeningHumanExplanation | None = Field(
        default=None, alias="humanExplanation"
    )
    next_branches: list[OpeningContinuation] = Field(alias="nextBranches", default_factory=list)
    source: str = "Lichess chess-openings (CC0-1.0)"
    authority_boundary: str = Field(
        alias="authorityBoundary",
        default=(
            "开局目录只提供经过验证的名称、ECO和走法路径；"
            "当前局面评价、走法优劣和战略结论仍由Stockfish与程序事实层决定。"
        ),
    )


class OpeningPresentation(BaseModel):
    """Conservative, user-facing opening context backed by a verified path."""

    model_config = ConfigDict(populate_by_name=True)

    opening_id: str = Field(alias="openingId")
    eco: str
    name: str
    family_name: str = Field(alias="familyName")
    family_name_zh: str = Field(alias="familyNameZh")
    variation_path: list[str] = Field(alias="variationPath", default_factory=list)
    variation_name_zh: str | None = Field(alias="variationNameZh", default=None)
    display_name: str = Field(alias="displayName")
    match_type: Literal["exact_path", "path_prefix", "position_transposition", "exact_fen"] = Field(
        alias="matchType"
    )
    matched_ply: int = Field(alias="matchedPly", ge=1)
    query_ply: int = Field(alias="queryPly", ge=1)
    confidence: Literal["exact", "high"]
    description: str
    white_plan: str = Field(alias="whitePlan")
    black_plan: str = Field(alias="blackPlan")
    tactical_themes: list[str] = Field(alias="tacticalThemes", default_factory=list)
    source: str = "Lichess chess-openings (CC0-1.0)"
    resolver_version: str = Field(
        alias="resolverVersion", default=OPENING_CONTEXT_RESOLVER_VERSION
    )
    database_revision: str = Field(alias="databaseRevision", default="unknown")

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "identityAuthority": "program_confirmed",
            "openingId": self.opening_id,
            "eco": self.eco,
            "name": self.name,
            "familyName": self.family_name,
            "variationPath": self.variation_path,
            "matchType": self.match_type,
            "matchedPly": self.matched_ply,
            "queryPly": self.query_ply,
            "background": {
                "description": self.description,
                "whitePlan": self.white_plan,
                "blackPlan": self.black_plan,
                "tacticalThemes": self.tactical_themes,
            },
            "policy": (
                "名称和变例由程序确定，不得重新命名。背景只说明该开局的常见思路；"
                "只有当前事实包另有支持时，才能把常见思路写成当前局面的事实或计划。"
            ),
        }


_OPENING_FAMILY_PROFILES: dict[str, dict[str, Any]] = {
    "Italian Game": {
        "zh": "意大利开局",
        "description": "这是一个经典开放型布局，双方通过快速发展子力争夺中心。",
        "white": "白方常通过准备并推进d4打开中心，让双象和王翼子力获得更活跃的空间。",
        "black": "黑方通常完成王翼发展，并寻找d5反击来直接挑战白方中心。",
        "themes": ["中心突破d4", "黑方反击d5", "e5兵与f7弱点", "王的安全"],
    },
    "Ruy Lopez": {
        "zh": "西班牙开局",
        "description": "这是一个经典开放型布局，双方围绕中心控制和子力协调展开长期较量。",
        "white": "白方通常保持对中心的压力，完成发展后再选择中心或王翼行动。",
        "black": "黑方通常巩固e5兵，并通过协调后翼子力和中心反击争取平衡。",
        "themes": ["e5兵压力", "中心张力", "子力重组", "王的安全"],
    },
    "Sicilian Defense": {
        "zh": "西西里防御",
        "description": "这是一个不对称的半开放型布局，双方往往在不同区域争取主动。",
        "white": "白方通常利用空间和发展速度在中心或王翼组织行动。",
        "black": "黑方通常利用c线和后翼兵形制造反击，并持续挑战中心。",
        "themes": ["不对称兵形", "开放c线", "中心突破", "异侧进攻"],
    },
    "French Defense": {
        "zh": "法兰西防御",
        "description": "这是一个结构鲜明的半开放型布局，中心兵链决定双方主要行动方向。",
        "white": "白方通常利用空间优势，在中心和王翼寻找突破。",
        "black": "黑方通常攻击白方兵链根部，并用c5或f6反击中心。",
        "themes": ["兵链攻防", "c5反击", "f6反击", "后翼空间"],
    },
    "Caro-Kann Defense": {
        "zh": "卡罗-康防御",
        "description": "这是一个以稳固兵形和顺利发展为目标的半开放型布局。",
        "white": "白方通常利用空间优势保持中心压力，并争取更主动的子力部署。",
        "black": "黑方通常先完成稳健发展，再用c5或e5挑战白方中心。",
        "themes": ["稳固兵形", "中心挑战", "轻子协调", "残局结构"],
    },
    "Queen's Gambit": {
        "zh": "后翼弃兵",
        "description": "这是一个经典封闭型布局，核心是用翼兵交换争取中心控制。",
        "white": "白方通常争取建立强中心，并利用开放线路发展后翼子力。",
        "black": "黑方通常选择稳固中心或及时归还兵，以完成发展并寻找反击。",
        "themes": ["中心控制", "c线活动", "少数兵进攻", "孤后兵结构"],
    },
    "English Opening": {
        "zh": "英国式开局",
        "description": "这是一个灵活的侧翼开局，常通过后翼控制间接影响中心。",
        "white": "白方通常保持兵形弹性，逐步加强对中心和后翼关键格的控制。",
        "black": "黑方可以直接占据中心，也可以采用对称结构后寻找及时突破。",
        "themes": ["后翼空间", "中心反击", "长对角线", "兵形转换"],
    },
    "King's Indian Defense": {
        "zh": "王印度防御",
        "description": "这是一个动态封闭型布局，黑方允许白方占据中心后再发动反击。",
        "white": "白方通常利用中心空间在后翼推进，并限制黑方反击速度。",
        "black": "黑方通常攻击白方中心，并在王翼寻找主动行动。",
        "themes": ["中心兵链", "王翼进攻", "后翼扩张", "中心反击"],
    },
}


_VARIATION_NAMES_ZH = {
    "Giuoco Piano": "吉奥科钢琴变化",
    "Giuoco Pianissimo": "吉奥科皮亚尼西莫变化",
    "Classical Variation": "古典变化",
    "Center Attack": "中心进攻变化",
    "Greco's Attack": "格列柯进攻",
    "Greco Gambit": "格列柯弃兵",
    "Closed": "封闭体系",
    "Classical Main Line": "古典主线",
    "Classical Development": "古典发展体系",
    "Najdorf Variation": "纳吉多夫变化",
    "English Attack": "英国式进攻",
    "Winawer Variation": "维纳维尔变化",
    "Orthodox Defense": "正统防御",
    "Rubinstein Variation": "鲁宾斯坦变化",
    "Mar del Plata Variation": "马德普拉塔变化",
    "Exchange Variation": "兑换变化",
    "Four Knights Variation": "四马变化",
    "Classical Attack": "古典进攻",
    "Austrian Attack": "奥地利进攻",
    "Leningrad Variation": "列宁格勒变化",
    "Vienna Gambit": "维也纳弃兵",
    "Open Defense": "开放变化",
    "Two Knights Defense": "双马防御",
    "Evans Gambit": "伊文斯弃兵",
    "Symmetrical Variation": "对称变化",
    "Stonewall Variation": "石墙变化",
    "Advance Variation": "推进变化",
    "Euwe Variation": "欧威变化",
    "Paulsen Attack": "保尔森进攻",
    "Richter-Rauzer Attack": "里赫特尔-劳泽尔进攻",
    "Dragon Variation": "龙式变化",
    "Yugoslav Attack": "南斯拉夫进攻",
    "Scheveningen Variation": "舍维宁根变化",
    "Tarrasch Variation": "塔拉什变化",
    "Closed Main Line": "封闭主线",
    "Spanish Variation": "西班牙变化",
    "Meran Variation": "梅兰变化",
    "Main Line": "主线",
}


_FAMILY_NAMES_ZH = {
    "Alekhine Defense": "阿廖欣防御",
    "Benko Gambit": "本科弃兵",
    "Benoni Defense": "别诺尼防御",
    "Bird Opening": "伯德开局",
    "Bishop's Opening": "象开局",
    "Blackmar-Diemer Gambit": "布莱克马-迪默弃兵",
    "Bogo-Indian Defense": "博戈印度防御",
    "Caro-Kann Defense": "卡罗-康防御",
    "Catalan Opening": "加泰罗尼亚开局",
    "Center Game": "中心开局",
    "Colle System": "科列体系",
    "Danish Gambit": "丹麦弃兵",
    "Dutch Defense": "荷兰防御",
    "English Defense": "英国式防御",
    "English Opening": "英国式开局",
    "Englund Gambit": "英格伦弃兵",
    "Four Knights Game": "四马开局",
    "French Defense": "法兰西防御",
    "Grünfeld Defense": "格林菲尔德防御",
    "Indian Defense": "印度防御",
    "Italian Game": "意大利开局",
    "King's Gambit": "王翼弃兵",
    "King's Indian Attack": "王印度进攻",
    "King's Indian Defense": "王印度防御",
    "King's Knight Opening": "王翼马开局",
    "King's Pawn Game": "王兵开局",
    "King's Pawn Opening": "王兵开局",
    "Latvian Gambit": "拉脱维亚弃兵",
    "London System": "伦敦体系",
    "Modern Defense": "现代防御",
    "Neo-Grünfeld Defense": "新格林菲尔德防御",
    "Nimzo-Indian Defense": "尼姆佐印度防御",
    "Nimzo-Larsen Attack": "尼姆佐-拉尔森进攻",
    "Nimzowitsch Defense": "尼姆佐维奇防御",
    "Old Indian Defense": "老印度防御",
    "Owen Defense": "欧文防御",
    "Petrov's Defense": "彼得罗夫防御",
    "Philidor Defense": "菲利多尔防御",
    "Pirc Defense": "皮尔茨防御",
    "Polish Opening": "波兰开局",
    "Ponziani Opening": "庞齐亚尼开局",
    "Queen's Gambit": "后翼弃兵",
    "Queen's Gambit Accepted": "后翼弃兵接受变化",
    "Queen's Gambit Declined": "后翼弃兵拒绝变化",
    "Queen's Indian Defense": "后印度防御",
    "Queen's Pawn Game": "后兵开局",
    "Réti Opening": "列蒂开局",
    "Richter-Veresov Attack": "里赫特尔-韦列索夫进攻",
    "Ruy Lopez": "西班牙开局",
    "Scandinavian Defense": "斯堪的纳维亚防御",
    "Scotch Game": "苏格兰开局",
    "Semi-Slav Defense": "半斯拉夫防御",
    "Sicilian Defense": "西西里防御",
    "Slav Defense": "斯拉夫防御",
    "Tarrasch Defense": "塔拉什防御",
    "Torre Attack": "托雷进攻",
    "Trompowsky Attack": "特罗姆波夫斯基进攻",
    "Vienna Game": "维也纳开局",
    "Zukertort Opening": "朱克托特开局",
}


_OPENING_TERM_NAMES_ZH = {
    "Accepted": "接受变化",
    "Declined": "拒绝变化",
    "Defense": "防御",
    "Gambit": "弃兵",
    "Opening": "开局",
    "Attack": "进攻",
    "System": "体系",
    "Formation": "阵型",
    "Game": "开局",
    "Main Line": "主线",
    "Classical Variation": "古典变化",
    "Modern Variation": "现代变化",
}


def _translate_opening_label(
    name: str,
    *,
    eco: str | None = None,
    fallback: str | None = None,
) -> str:
    exact = _FAMILY_NAMES_ZH.get(name) or _VARIATION_NAMES_ZH.get(name)
    if exact:
        return exact
    translated = name
    for english, chinese in sorted(
        _OPENING_TERM_NAMES_ZH.items(), key=lambda item: len(item[0]), reverse=True
    ):
        translated = translated.replace(english, chinese)
    if translated != name and not any("a" <= char.lower() <= "z" for char in translated):
        return translated
    return fallback or (f"{eco} 开局体系" if eco else "开局体系")


def _fallback_opening_profile(opening: OpeningMatch) -> dict[str, Any]:
    moves = opening.uci_moves
    first = moves[0] if moves else ""
    reply = moves[1] if len(moves) > 1 else ""
    if first == "e2e4" and reply == "e7e5":
        return {
            "description": "这是开放型王兵开局，双方通常直接争夺中心并快速发展王翼子力。",
            "white": "白方通常争取顺畅出子、尽早保护王，并寻找d4中心突破。",
            "black": "黑方通常维持e5中心支点、完成王翼发展，并在合适时机用d5反击。",
            "themes": ["快速发展", "中心争夺", "王的安全"],
        }
    if first == "e2e4" and reply == "c7c5":
        return {
            "description": "这是不对称的王兵开局，黑方从侧翼立即争夺d4中心格。",
            "white": "白方通常利用空间和发展速度，在中心或王翼组织主动行动。",
            "black": "黑方通常利用半开放c线和后翼兵形制造反击。",
            "themes": ["不对称兵形", "开放c线", "中心突破"],
        }
    if first == "e2e4":
        return {
            "description": "这是王兵开局体系，双方围绕e4中心兵和中心控制安排发展。",
            "white": "白方通常利用先行优势发展子力，并准备扩大中心或王翼空间。",
            "black": "黑方通常先挑战白方中心，再根据兵形选择稳固发展或反击。",
            "themes": ["中心控制", "子力发展", "兵链攻防"],
        }
    if first == "d2d4" and reply == "g8f6":
        return {
            "description": "这是印度防御类后兵体系，黑方先发展子力，再从侧面攻击白方中心。",
            "white": "白方通常建立空间中心，并根据黑方部署选择推进或稳固中心。",
            "black": "黑方通常用子力和兵的突破向白方中心施压，争取动态反击。",
            "themes": ["中心兵链", "侧翼反击", "子力协调"],
        }
    if first == "d2d4":
        return {
            "description": "这是后兵开局体系，双方通常围绕d4、d5和c线进行长期中心较量。",
            "white": "白方通常巩固中心、发展后翼子力，并寻找c4或e4扩张。",
            "black": "黑方通常保持中心稳定，并通过c5或e5反击白方中心。",
            "themes": ["中心张力", "c线活动", "兵形转换"],
        }
    return {
        "description": "这是灵活的侧翼开局体系，先控制关键中心格，再根据对手部署转换兵形。",
        "white": "白方通常保持兵形弹性，先发展子力，再选择合适的中心推进。",
        "black": "黑方通常争取占据中心，并用及时的兵突破限制白方侧翼布局。",
        "themes": ["灵活发展", "中心控制", "兵形转换"],
    }


def _normalize_opening_explanation_zh(text: str) -> str | None:
    raw_paragraphs = re.split(r"\n\s*\n+", text.replace("\u200b", "").strip())
    replacements = {
        "White": "白方",
        "white": "白方",
        "Black": "黑方",
        "black": "黑方",
        "怀特": "白方",
        "布莱克": "黑方",
        "白棋": "白方",
        "黑棋": "黑方",
        "白牌": "白方",
        "黑牌": "黑方",
        "白色方": "白方",
        "黑色方": "黑方",
        "主教": "象",
        "骑士": "马",
    }
    normalized_paragraphs: list[str] = []
    for raw_paragraph in raw_paragraphs:
        normalized = " ".join(raw_paragraph.split())
        for source, target in replacements.items():
            normalized = re.sub(
                rf"(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])", target, normalized
            )
        # Chinese piece names can be attached to an untranslated Latin modifier,
        # for example "fianchettoed主教"; they still need standard chess terms.
        normalized = normalized.replace("主教", "象").replace("骑士", "马")
        normalized = re.sub(r"\b([a-h])-pawn\b", r"\1兵", normalized, flags=re.IGNORECASE)
        normalized = re.sub(
            r"/\s*(\d+)\s*(\.\.\.|[。.])\s*([^/，。；;]+?)/",
            _normalized_move_marker,
            normalized,
        )
        normalized = re.sub(
            r"(?<!\d)(\d+)\s*[。.][ ]*\.\s*\.\s*([KQRBNOa-h])", r"\1...\2", normalized
        )
        normalized = re.sub(r"(?<!\d)(\d+)\s*[。.][ ]+([KQRBNOa-h])", r"\1.\2", normalized)
        normalized = re.sub(r"(?<!\d)(\d+)\.\.\.[ ]+([KQRBNOa-h])", r"\1...\2", normalized)
        normalized = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff\d])", "", normalized)
        normalized = re.sub(r"[ \t]+([，。！？；：,.!?;:])", r"\1", normalized)
        normalized = re.sub(r"([，。！？；：])(?=[A-Za-z0-9])", r"\1 ", normalized)
        normalized = _normalize_castle_term_zh(normalized)
        if normalized:
            normalized_paragraphs.append(normalized)
    normalized = "\n\n".join(normalized_paragraphs)
    # Reject legacy mojibake instead of exposing it as a Chinese book explanation.
    latin1_noise = len(re.findall(r"[À-ÿ]", normalized))
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    if chinese_chars < 8 or latin1_noise > max(4, chinese_chars // 8):
        return None
    return normalized


def _normalize_castle_term_zh(text: str) -> str:
    """Distinguish the rook piece from machine-translated castling language."""
    castling_phrases = {
        "城堡权利": "易位权",
        "城堡王侧": "王翼易位",
        "城堡王翼": "王翼易位",
        "王侧城堡": "王翼易位",
        "王翼城堡": "王翼易位",
        "城堡后侧": "后翼易位",
        "城堡后翼": "后翼易位",
        "后侧城堡": "后翼易位",
        "后翼城堡": "后翼易位",
        "长城堡": "长易位",
        "短城堡": "短易位",
        "相反城堡": "异向易位",
        "对面城堡": "异向易位",
        "城堡国王": "已易位的王",
    }
    for source, target in castling_phrases.items():
        text = text.replace(source, target)

    segments = re.split(r"(?<=[。！？\n])", text)
    castling_cues = (
        "O-O",
        "易位",
        "王侧",
        "王翼",
        "后侧",
        "后翼",
        "国王",
        "易位权",
        "准备",
        "无法",
        "不能",
        "可以",
        "应该",
        "必须",
        "通常",
        "经常",
        "很快",
        "之后",
        "之前",
        "选择",
        "延迟",
        "建立",
        "建造",
        "建城",
    )
    normalized_segments: list[str] = []
    for segment in segments:
        if "城堡" not in segment:
            normalized_segments.append(segment)
            continue
        replacement = "易位" if any(cue in segment for cue in castling_cues) else "车"
        normalized_segments.append(segment.replace("城堡", replacement))
    return "".join(normalized_segments)


_OPENING_LANGUAGE_REJECT_MARKERS = (
    "意大利足球",
    "电子棋子",
    "电子兵",
    "黑人玩家",
    "白人玩家",
    "黑人",
    "白人",
    "大多数黑人",
    "女王的印第安人",
    "加泰罗尼亚公开赛",
    "加泰罗尼亚语",
    "柏林号",
    "后防线",
    "白方的小子",
    "用剑",
    "双足象",
    "七国集团",
    "最先进的a兵",
    "侧翼棋子换成更有价值的中央棋子",
    "开放举措",
    "独立的台词",
    "国王的策略",
    "接受策略",
    "拒绝策略",
    "斯汤顿策略",
    "反策略",
    "开放理论",
    "播放 e5",
    "播放 c5",
    "播放 d5",
    "游戏继续",
    "示例行为",
    "选择更安静的选择",
    "英语开场",
    "明显的夺回",
    "马节奏",
    "该脚",
    "先进但较弱的棋子",
    "蒙面攻击",
    "开发了他们",
    "向象施压",
)


def _opening_explanation_language_is_natural(text: str | None) -> bool:
    if not text:
        return False
    if any(marker in text for marker in _OPENING_LANGUAGE_REJECT_MARKERS):
        return False
    # Slash-wrapped move markers or a dangling move number are damaged source
    # fragments, not readable Chinese prose.
    if re.search(r"/\s*\d+[。.](?:\.\.)?", text):
        return False
    if any(re.fullmatch(r"\d+\.?\.?", paragraph.strip()) for paragraph in text.split("\n\n")):
        return False
    return True


def _normalized_move_marker(match: re.Match[str]) -> str:
    move_number = match.group(1).strip()
    black_dots = "..." if match.group(2) == "..." else "."
    move = re.sub(r"\s+", "", match.group(3))
    return f"{move_number}{black_dots}{move}"


def _concise_opening_explanation(text: str, *, limit: int = 900) -> str | None:
    normalized = _normalize_opening_explanation_zh(text)
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized

    selected: list[str] = []
    used = 0
    for paragraph in normalized.split("\n\n"):
        extra = len(paragraph) + (2 if selected else 0)
        if used + extra > limit:
            break
        selected.append(paragraph)
        used += extra
    if selected:
        return "\n\n".join(selected)

    # Very long source paragraphs are clipped only at a real Chinese sentence
    # boundary. A period inside SAN (for example 7.Bd3) is never treated as one.
    sentence_end = max(normalized.rfind(mark, 0, limit + 1) for mark in ("。", "！", "？"))
    return normalized[: sentence_end + 1] if sentence_end >= 40 else None


def _position_key(board: chess.Board) -> str:
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    return " ".join((
        board.board_fen(),
        "w" if board.turn == chess.WHITE else "b",
        board.castling_xfen() or "-",
        ep,
    ))


def _parse_pgn(pgn: str) -> tuple[list[str], chess.Board, bool]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("无法解析PGN")
    if game.errors:
        raise ValueError(f"PGN包含非法走法：{game.errors[0]}")
    board = game.board()
    starts_from_standard_position = board.fen() == chess.STARTING_FEN
    moves: list[str] = []
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError(f"PGN包含非法走法：{move.uci()}")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise ValueError("PGN中没有可识别的主线走法")
    return moves, board, starts_from_standard_position


class OpeningKnowledgeRepository:
    """Read-only deterministic opening lookup; never evaluates the position."""

    def __init__(
        self,
        catalog_path: Path | str = DEFAULT_OPENING_CATALOG,
        explanation_path: Path | str | None = DEFAULT_OPENING_EXPLANATIONS,
        extension_path: Path | str | None = DEFAULT_CLASSIC_OPENING_EXTENSIONS,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.explanation_path = Path(explanation_path) if explanation_path else None
        if (
            extension_path == DEFAULT_CLASSIC_OPENING_EXTENSIONS
            and self.catalog_path != DEFAULT_OPENING_CATALOG
        ):
            self.extension_path = None
        else:
            self.extension_path = Path(extension_path) if extension_path else None
        self._entries: list[dict[str, Any]] | None = None
        self._path_index: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        self._path_coverage_index: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        self._position_index: dict[str, list[tuple[dict[str, Any], int]]] = {}
        self._explanation_index: dict[tuple[str, ...], dict[str, Any]] = {}
        self._database_revision = "unknown"

    def lookup(self, *, pgn: str | None = None, fen: str | None = None) -> OpeningLookupResponse:
        self._ensure_loaded()
        query_moves: list[str] = []
        allow_path_match = False
        if pgn:
            query_moves, board, allow_path_match = _parse_pgn(pgn)
        elif fen:
            try:
                board = chess.Board(fen)
            except ValueError as exc:
                raise ValueError(f"无效FEN：{exc}") from exc
        else:
            raise ValueError("pgn或fen至少提供一个")

        if fen and pgn:
            try:
                supplied = chess.Board(fen)
            except ValueError as exc:
                raise ValueError(f"无效FEN：{exc}") from exc
            if _position_key(supplied) != _position_key(board):
                raise ValueError("PGN终点局面与提供的FEN不一致")

        return self._lookup_resolved(
            query_moves=query_moves,
            board=board,
            allow_path_match=allow_path_match,
        )

    def lookup_moves(
        self,
        uci_moves: Sequence[str],
        *,
        initial_fen: str = chess.STARTING_FEN,
    ) -> OpeningLookupResponse:
        """Look up one already parsed main-line prefix without involving an LLM."""
        self._ensure_loaded()
        try:
            board = chess.Board(initial_fen)
        except ValueError as exc:
            raise ValueError(f"无效初始FEN：{exc}") from exc
        starts_from_standard_position = board.fen() == chess.STARTING_FEN
        query_moves: list[str] = []
        for raw_uci in uci_moves:
            try:
                move = chess.Move.from_uci(raw_uci)
            except ValueError as exc:
                raise ValueError(f"走法序列包含无效UCI：{raw_uci}") from exc
            if move not in board.legal_moves:
                raise ValueError(f"走法序列包含非法走法：{raw_uci}")
            query_moves.append(move.uci())
            board.push(move)
        if not query_moves:
            raise ValueError("走法序列为空")
        return self._lookup_resolved(
            query_moves=query_moves,
            board=board,
            allow_path_match=starts_from_standard_position,
        )

    def presentation_for_moves(
        self,
        uci_moves: Sequence[str],
        *,
        initial_fen: str = chess.STARTING_FEN,
    ) -> OpeningPresentation | None:
        result = self.lookup_moves(uci_moves, initial_fen=initial_fen)
        query_path = tuple(uci_moves)
        path_fully_covered = (
            initial_fen == chess.STARTING_FEN
            and query_path in self._path_coverage_index
        )
        return self.presentation_from_lookup(
            result,
            catalog_position_confirmed=(
                path_fully_covered or result.match_type == "position_transposition"
            ),
            database_revision=self._database_revision,
        )

    @staticmethod
    def presentation_from_lookup(
        result: OpeningLookupResponse,
        *,
        catalog_position_confirmed: bool = False,
        database_revision: str = "unknown",
    ) -> OpeningPresentation | None:
        opening = result.opening
        if (
            not catalog_position_confirmed
            or not result.matched
            or opening is None
            or result.match_type == "none"
        ):
            return None
        profile = _OPENING_FAMILY_PROFILES.get(opening.family_name)
        if profile is None:
            profile = _fallback_opening_profile(opening)
            family_name_zh = _translate_opening_label(opening.family_name, eco=opening.eco)
        else:
            family_name_zh = str(profile["zh"])
        translated_variations = [
            _translate_opening_label(name, fallback=f"第{index}级数据库分支")
            for index, name in enumerate(opening.variation_path, start=1)
        ]
        variation_zh = " · ".join(translated_variations) or None
        display_name = family_name_zh
        if variation_zh:
            display_name += f" · {variation_zh}"
        confidence = "exact" if result.match_type in {"exact_path", "position_transposition", "exact_fen"} else "high"
        book_explanation = _concise_opening_explanation(
            result.human_explanation.text
        ) if result.human_explanation else None
        if not _opening_explanation_language_is_natural(book_explanation):
            book_explanation = None
        return OpeningPresentation(
            openingId=opening.opening_id,
            eco=opening.eco,
            name=opening.name,
            familyName=opening.family_name,
            familyNameZh=family_name_zh,
            variationPath=opening.variation_path,
            variationNameZh=variation_zh,
            displayName=display_name,
            matchType=result.match_type,
            matchedPly=opening.matched_ply,
            queryPly=result.query_ply,
            confidence=confidence,
            description=book_explanation or profile["description"],
            whitePlan=profile["white"],
            blackPlan=profile["black"],
            tacticalThemes=list(profile["themes"]),
            source=result.source,
            databaseRevision=database_revision,
        )

    def _lookup_resolved(
        self,
        *,
        query_moves: list[str],
        board: chess.Board,
        allow_path_match: bool,
    ) -> OpeningLookupResponse:
        position_matches = self._position_index.get(_position_key(board), [])
        exact_path_matches = (
            self._path_index.get(tuple(query_moves), [])
            if query_moves and allow_path_match
            else []
        )
        covered_path_matches = (
            self._path_coverage_index.get(tuple(query_moves), [])
            if query_moves and allow_path_match
            else []
        )
        entry: dict[str, Any] | None = None
        matched_ply: int | None = None
        match_type: Literal["exact_path", "path_prefix", "position_transposition", "exact_fen", "none"] = "none"

        if exact_path_matches:
            entry = self._select(exact_path_matches)
            match_type = "exact_path"
        elif covered_path_matches:
            for length in range(len(query_moves) - 1, 0, -1):
                matches = self._path_index.get(tuple(query_moves[:length]), [])
                if matches:
                    entry = self._select(matches)
                    match_type = "path_prefix"
                    break
            if entry is None:
                entry = self._select_shortest(covered_path_matches)
                matched_ply = len(query_moves)
                match_type = "path_prefix"
        elif position_matches:
            entry, position_ply = self._select_position(position_matches)
            matched_ply = position_ply
            match_type = "position_transposition" if query_moves else "exact_fen"
        elif query_moves and allow_path_match:
            for length in range(len(query_moves) - 1, 0, -1):
                matches = self._path_index.get(tuple(query_moves[:length]), [])
                if matches:
                    entry = self._select(matches)
                    match_type = "path_prefix"
                    break

        explanation_moves = query_moves or (entry["uciMoves"] if entry else [])
        return OpeningLookupResponse(
            matched=entry is not None,
            matchType=match_type,
            queryPly=len(query_moves),
            currentFen=board.fen(),
            opening=self._to_match(entry, matched_ply=matched_ply) if entry else None,
            humanExplanation=self._find_explanation(explanation_moves),
            nextBranches=self._next_branches(query_moves),
            source=(
                "Pawnlab 经典开局主线扩展（python-chess 合法性校验）"
                if entry and entry.get("classicExtension")
                else "Lichess chess-openings (CC0-1.0)"
            ),
        )

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        if not self.catalog_path.exists():
            raise RuntimeError(f"开局目录不存在：{self.catalog_path}")
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        entries = list(payload.get("openings", []))
        revisions = [str(payload.get("source", {}).get("revision") or payload.get("schemaVersion") or "unknown")]
        if self.extension_path and self.extension_path.exists():
            extension_payload = json.loads(self.extension_path.read_text(encoding="utf-8"))
            revisions.append(
                str(
                    extension_payload.get("source", {}).get("revision")
                    or extension_payload.get("schemaVersion")
                    or "unknown"
                )
            )
            known_ids = {entry["openingId"] for entry in entries}
            entries.extend(
                {**entry, "classicExtension": True}
                for entry in extension_payload.get("openings", [])
                if entry["openingId"] not in known_ids
            )
        path_index: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        path_coverage_index: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        position_index: dict[str, list[tuple[dict[str, Any], int]]] = {}
        for entry in entries:
            moves = tuple(entry["uciMoves"])
            path_index.setdefault(moves, []).append(entry)
            board = chess.Board()
            for length in range(1, len(moves) + 1):
                path_coverage_index.setdefault(moves[:length], []).append(entry)
                board.push_uci(moves[length - 1])
                position_index.setdefault(_position_key(board), []).append((entry, length))
        self._entries = entries
        self._path_index = path_index
        self._path_coverage_index = path_coverage_index
        self._position_index = position_index
        self._database_revision = "+".join(revisions)
        if self.explanation_path and self.explanation_path.exists():
            explanation_payload = json.loads(
                self.explanation_path.read_text(encoding="utf-8")
            )
            explanation_revision = str(
                explanation_payload.get("generatedAt")
                or explanation_payload.get("schemaVersion")
                or "unknown"
            )
            self._database_revision += f"+{explanation_revision}"
            self._explanation_index = {
                tuple(item["uciMoves"]): item
                for item in explanation_payload.get("explanations", [])
            }

    @staticmethod
    def _select(entries: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            entries,
            key=lambda item: (
                bool(item.get("classicExtension")),
                item["plyCount"],
                item["eco"],
                item["name"],
            ),
        )[-1]

    @staticmethod
    def _select_shortest(entries: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(entries, key=lambda item: (item["plyCount"], item["eco"], item["name"]))[0]

    @classmethod
    def _select_position(
        cls,
        candidates: list[tuple[dict[str, Any], int]],
    ) -> tuple[dict[str, Any], int]:
        """Prefer a name reached at this node; otherwise use the nearest future name."""
        reached = [item for item in candidates if item[0]["plyCount"] <= item[1]]
        if reached:
            return sorted(
                reached,
                key=lambda item: (
                    item[0]["plyCount"],
                    bool(item[0].get("classicExtension")),
                    item[0]["eco"],
                    item[0]["name"],
                ),
            )[-1]
        return sorted(
            candidates,
            key=lambda item: (item[0]["plyCount"], item[0]["eco"], item[0]["name"]),
        )[0]

    @staticmethod
    def _to_match(entry: dict[str, Any], *, matched_ply: int | None = None) -> OpeningMatch:
        return OpeningMatch(
            openingId=entry["openingId"],
            eco=entry["eco"],
            name=entry["name"],
            familyName=entry["familyName"],
            variationPath=entry["variationPath"],
            pgn=entry["pgn"],
            sanMoves=entry["sanMoves"],
            uciMoves=entry["uciMoves"],
            matchedPly=matched_ply or entry["plyCount"],
        )

    def _next_branches(self, query_moves: list[str], *, limit: int = 12) -> list[OpeningContinuation]:
        if not query_moves or self._entries is None:
            return []
        prefix = tuple(query_moves)
        branches: dict[str, OpeningContinuation] = {}
        for entry in self._entries:
            moves = tuple(entry["uciMoves"])
            if len(moves) <= len(prefix) or moves[:len(prefix)] != prefix:
                continue
            next_uci = moves[len(prefix)]
            branches.setdefault(next_uci, OpeningContinuation(
                san=entry["sanMoves"][len(prefix)],
                uci=next_uci,
                openingName=entry["name"],
                eco=entry["eco"],
            ))
        return sorted(branches.values(), key=lambda item: (item.san, item.opening_name))[:limit]

    def _find_explanation(
        self, moves: list[str]
    ) -> OpeningHumanExplanation | None:
        for length in range(len(moves), 0, -1):
            item = self._explanation_index.get(tuple(moves[:length]))
            if item:
                return OpeningHumanExplanation(
                    text=item.get("textZh") or item["text"],
                    text_en=item.get("textEn") or item.get("text"),
                    text_zh=item.get("textZh"),
                    matchedPly=length,
                    pageTitle=item["pageTitle"],
                    pageUrl=item["pageUrl"],
                    revisionId=item["revisionId"],
                    license=item["license"],
                    attribution=item["attribution"],
                )
        return None
