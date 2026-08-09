from __future__ import annotations

import argparse
import bz2
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import chess


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "app" / "data" / "opening-path-catalog.json"
RESEARCH_OUTPUT = ROOT / "docs" / "research" / "phase8e-opening-explanations.json"
RUNTIME_OUTPUT = ROOT / "app" / "data" / "opening-explanations.json"
MANIFEST = ROOT / "docs" / "research" / "phase8e-opening-explanations-manifest.json"
DUMP = ROOT / "work" / "research_books" / "enwikibooks-latest-pages-articles.xml.bz2"
API = "https://en.wikibooks.org/w/api.php"
PREFIX = "Chess Opening Theory/"
LICENSE = "CC BY-SA 4.0 / GFDL"
USER_AGENT = "ChessAnalysisOpeningResearch/1.0"


def api_get(params: dict) -> dict:
    url = API + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(6):
        try:
            with urlopen(request, timeout=40) as response:
                payload = json.load(response)
            time.sleep(0.35)
            return payload
        except HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 12)
            time.sleep(max(1.0, min(delay, 30.0)))
        except URLError:
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 12))
    raise RuntimeError("Wikibooks API retry loop exhausted")


def fetch_page_index(fetch: Callable[[dict], dict] = api_get) -> list[dict]:
    pages: list[dict] = []
    continuation: dict = {}
    while True:
        payload = fetch({
            "action": "query", "list": "allpages", "apprefix": PREFIX,
            "apnamespace": "0", "aplimit": "500", "format": "json",
            "formatversion": "2", **continuation,
        })
        pages.extend(payload["query"]["allpages"])
        if "continue" not in payload:
            return pages
        continuation = payload["continue"]


def parse_title_path(title: str) -> tuple[list[str], list[str]] | None:
    if not title.startswith(PREFIX):
        return None
    board = chess.Board()
    san_moves: list[str] = []
    uci_moves: list[str] = []
    for segment in title[len(PREFIX):].split("/"):
        token = re.sub(r"^\d+\.{1,3}", "", segment).strip()
        token = token.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
        token = token.rstrip("?!")
        if not token:
            return None
        try:
            move = board.parse_san(token)
        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            return None
        san_moves.append(board.san(move))
        uci_moves.append(move.uci())
        board.push(move)
    return san_moves, uci_moves


def clean_extract(text: str, *, max_chars: int = 5000) -> str:
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    kept: list[str] = []
    for line in lines:
        lowered = line.casefold()
        if lowered in {
            "theory table", "references", "external links", "navigation",
            "see also", "bibliography",
        }:
            break
        if not line or re.fullmatch(r"[.·*\-\s]+", line):
            continue
        kept.append(line)
    result = "\n\n".join(kept)
    result = re.sub(r"[ \t]+", " ", result).strip()
    if len(result) > max_chars:
        boundary = result.rfind(".", 0, max_chars)
        result = result[: boundary + 1 if boundary >= 300 else max_chars].strip()
    return result


def clean_wikitext(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref\b[^>/]*/>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\{\|.*?\|\}", "", text, flags=re.DOTALL)
    for _ in range(8):
        reduced = re.sub(r"\{\{[^{}]*\}\}", "", text, flags=re.DOTALL)
        if reduced == text:
            break
        text = reduced
    text = re.sub(
        r"\[\[(?:File|Image|Category):[^\]]+\]\]", "", text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", "", text)
    text = re.sub(r"^=+\s*(.*?)\s*=+$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"^\s*[*#:;]+\s*", "", text, flags=re.MULTILINE)
    text = text.replace(" бд ", " · ")
    return clean_extract(html.unescape(text))


def fetch_explanations_from_dump(dump_path: Path) -> tuple[list[dict], dict]:
    explanations: list[dict] = []
    discovered = 0
    parseable = 0
    with bz2.open(dump_path, "rb") as stream:
        for _event, element in ET.iterparse(stream, events=("end",)):
            if not element.tag.endswith("}page"):
                continue
            title = element.findtext("./{*}title") or ""
            namespace = element.findtext("./{*}ns")
            if namespace != "0" or not title.startswith(PREFIX):
                element.clear()
                continue
            discovered += 1
            path = parse_title_path(title)
            if path is None:
                element.clear()
                continue
            parseable += 1
            revision = element.find("./{*}revision")
            if revision is None:
                element.clear()
                continue
            text = clean_wikitext(revision.findtext("./{*}text") or "")
            if len(text) < 120 or len(re.findall(r"[A-Za-z]{3,}", text)) < 20:
                element.clear()
                continue
            san_moves, uci_moves = path
            revision_id = int(revision.findtext("./{*}id") or "0")
            timestamp = revision.findtext("./{*}timestamp") or ""
            page_url = "https://en.wikibooks.org/wiki/" + quote(
                title.replace(" ", "_"), safe="/:()'!,.-"
            )
            explanations.append({
                "pageId": int(element.findtext("./{*}id") or "0"),
                "pageTitle": title,
                "pageUrl": page_url,
                "revisionId": revision_id,
                "revisionTimestamp": timestamp,
                "license": LICENSE,
                "attribution": "Wikibooks contributors",
                "sanMoves": san_moves,
                "uciMoves": uci_moves,
                "plyCount": len(uci_moves),
                "text": text,
            })
            element.clear()
    explanations.sort(key=lambda item: (item["uciMoves"], item["pageId"]))
    return explanations, {
        "discoveredPages": discovered,
        "parseableMovePathPages": parseable,
    }


def fetch_explanations(
    pages: list[dict], fetch: Callable[[dict], dict] = api_get
) -> list[dict]:
    parsed = {
        page["pageid"]: parse_title_path(page["title"])
        for page in pages
    }
    valid_pages = [page for page in pages if parsed[page["pageid"]]]
    explanations: list[dict] = []
    for start in range(0, len(valid_pages), 50):
        batch = valid_pages[start:start + 50]
        payload = fetch({
            "action": "query", "prop": "extracts|revisions",
            "pageids": "|".join(str(page["pageid"]) for page in batch),
            "explaintext": "1", "exsectionformat": "plain",
            "rvprop": "ids|timestamp", "format": "json", "formatversion": "2",
        })
        for page in payload["query"]["pages"]:
            text = clean_extract(page.get("extract", ""))
            if len(text) < 120 or len(re.findall(r"[A-Za-z]{3,}", text)) < 20:
                continue
            path = parsed[page["pageid"]]
            if path is None or not page.get("revisions"):
                continue
            san_moves, uci_moves = path
            revision = page["revisions"][0]
            page_url = "https://en.wikibooks.org/wiki/" + page["title"].replace(" ", "_")
            explanations.append({
                "pageId": page["pageid"],
                "pageTitle": page["title"],
                "pageUrl": page_url,
                "revisionId": revision["revid"],
                "revisionTimestamp": revision["timestamp"],
                "license": LICENSE,
                "attribution": "Wikibooks contributors",
                "sanMoves": san_moves,
                "uciMoves": uci_moves,
                "plyCount": len(uci_moves),
                "text": text,
            })
    explanations.sort(key=lambda item: (item["uciMoves"], item["pageId"]))
    return explanations


def coverage(explanations: list[dict], catalog: dict) -> dict:
    paths = {tuple(item["uciMoves"]) for item in explanations}
    covered = 0
    family_names: set[str] = set()
    for opening in catalog["openings"]:
        moves = opening["uciMoves"]
        if any(tuple(moves[:length]) in paths for length in range(len(moves), 0, -1)):
            covered += 1
            family_names.add(opening["familyName"])
    return {
        "catalogOpenings": len(catalog["openings"]),
        "coveredOpenings": covered,
        "coverageRate": round(covered / len(catalog["openings"]), 4),
        "coveredFamilies": len(family_names),
    }


def build_payload(explanations: list[dict], catalog: dict) -> dict:
    return {
        "schemaVersion": "opening-explanations-1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "title": "Chess Opening Theory",
            "project": "Wikibooks",
            "url": "https://en.wikibooks.org/wiki/Chess_Opening_Theory",
            "license": LICENSE,
            "attribution": "Wikibooks contributors",
            "authorityBoundary": (
                "Human opening explanations describe the source move path only. "
                "They do not establish the current engine evaluation or best move."
            ),
        },
        "summary": {
            "explanationCount": len(explanations),
            **coverage(explanations, catalog),
        },
        "explanations": explanations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-output", type=Path, default=RESEARCH_OUTPUT)
    parser.add_argument("--runtime-output", type=Path, default=RUNTIME_OUTPUT)
    parser.add_argument("--dump", type=Path, default=DUMP)
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if args.dump.exists():
        explanations, discovery = fetch_explanations_from_dump(args.dump)
    else:
        pages = fetch_page_index()
        explanations = fetch_explanations(pages)
        discovery = {
            "discoveredPages": len(pages),
            "parseableMovePathPages": sum(
                parse_title_path(page["title"]) is not None for page in pages
            ),
        }
    payload = build_payload(explanations, catalog)
    for path in (args.research_output, args.runtime_output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                payload, ensure_ascii=False,
                indent=2 if path == args.research_output else None,
                separators=None if path == args.research_output else (",", ":"),
            ),
            encoding="utf-8",
        )
    MANIFEST.write_text(json.dumps({
        "schemaVersion": "phase8e-opening-explanations-manifest-1.0",
        "generatedAt": payload["generatedAt"],
        "source": payload["source"],
        "summary": payload["summary"],
        **discovery,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
